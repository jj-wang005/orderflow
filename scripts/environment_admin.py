"""Explicit managed-schema administration. Never migrates/deletes the history DB."""
import argparse,base64,hashlib,json,os,re,secrets,sys
from datetime import datetime,date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine,text,select,inspect
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from app.core.config import Settings,ROOT,get_settings
from app.models import Base,EnvironmentIdentity
from scripts.demo_dataset import seed,verify,VERSION

NAMES={'demo':'order_demo','test':'order_test'}
USERS={'demo':'cvte_demo_local','test':'cvte_test_local'}
DELETE_ORDER=['manual_correction','order_change','ai_extraction','import_task_row','sales_order','import_task','configuration_version','match_rule','customer_config']

def admin_engine(database=None):
    # Administration is explicit; this connection is never a fallback for app/test queries.
    raw=dotenv_values(ROOT/'.env')
    s=Settings(_env_file=None,app_env='history',db_name='order_automation',db_host=raw.get('DB_HOST','127.0.0.1'),db_port=int(raw.get('DB_PORT','3306')),db_user=raw.get('DB_USER'),db_password=raw.get('DB_PASSWORD'))
    return create_engine(s.database_url.set(database=database),hide_parameters=True,pool_pre_ping=True)

def migrate(conn):
    name=conn.engine.url.database or ''
    if name not in NAMES.values() and not re.fullmatch(r'order_automation_envcheck_[a-f0-9]{32}',name):raise RuntimeError('迁移目标不在白名单')
    cfg=Config(str(ROOT/'alembic.ini'));cfg.set_main_option('script_location',str(ROOT/'alembic'));cfg.attributes['connection']=conn
    command.upgrade(cfg,'head');conn.commit()

def assert_target(conn,profile,require_identity=True):
    if profile not in NAMES or conn.scalar(text('SELECT DATABASE()'))!=NAMES[profile]:raise RuntimeError('目标库不符合固定环境，操作已拒绝')
    if require_identity:
        identity=conn.execute(text('SELECT profile,instance_id FROM environment_identity WHERE id=1')).mappings().one()
        local=json.loads((ROOT/'.runtime'/f'{profile}.identity.json').read_text(encoding='utf-8'))
        if identity['profile']!=profile or identity['instance_id']!=local['instance_id']:raise RuntimeError('数据库所有权标识不一致，禁止覆盖')
        if conn.scalar(text('SELECT version_num FROM alembic_version'))!='0008':raise RuntimeError('数据库版本不受当前重置脚本支持')
        if set(inspect(conn).get_table_names())!={*DELETE_ORDER,'environment_identity','alembic_version'}:raise RuntimeError('发现未知数据表，禁止不完整重置')

def initialize_all():
    engine=admin_engine()
    with engine.connect() as conn:
        names={r[0] for r in conn.exec_driver_sql('SHOW DATABASES')}
        if names&set(NAMES.values()):raise RuntimeError('目标数据库已存在，停止；没有覆盖或删除')
        for profile,user in USERS.items():
            if conn.scalar(text('SELECT count(*) FROM mysql.user WHERE User=:u'),{'u':user}):raise RuntimeError('目标运行账户已存在，停止；没有改写账户')
            if (ROOT/('.env.'+profile)).exists() or (ROOT/'.runtime'/f'{profile}.identity.json').exists():raise RuntimeError('环境配置或身份文件已存在，停止；不自动覆盖')
    # Preflight sample validity before any CREATE DATABASE.
    from scripts.demo_dataset import samples
    assert [len(rows) for c,p,rows in samples()]==[9,5,8]
    (ROOT/'.runtime').mkdir(exist_ok=True)
    root_settings=get_settings()
    for profile,name in NAMES.items():
        password=secrets.token_urlsafe(32);instance=str(uuid4());user=USERS[profile]
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
            conn.execute(text(f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY :password"),{'password':password})
            conn.exec_driver_sql(f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{name}`.* TO '{user}'@'127.0.0.1'")
        target=admin_engine(name)
        with target.connect() as conn:migrate(conn)
        with Session(target) as session,session.begin():
            session.add(EnvironmentIdentity(id=1,profile=profile,instance_id=instance,dataset_version=VERSION if profile=='demo' else 'demo-19-of-22-v1'))
            manifest=seed(session,profile);results=verify(session)
        env={'APP_ENV':profile,'DB_HOST':root_settings.db_host,'DB_PORT':str(root_settings.db_port),'DB_NAME':name,'DB_USER':user,'DB_PASSWORD':password,
             'AI_PROVIDER':'mimo' if profile=='demo' and root_settings.mimo_api_key.get_secret_value() else 'mock',
             'MIMO_EXTERNAL_ENABLED':'true' if profile=='demo' and root_settings.mimo_api_key.get_secret_value() else 'false',
             'MIMO_API_KEY':root_settings.mimo_api_key.get_secret_value() if profile=='demo' else '',
             'MIMO_TIMEOUT_SECONDS':'30','MIMO_RETRIES':'0','AI_MAX_PDF_PAGES':'5','AI_REQUESTS_PER_MINUTE':'20'}
        (ROOT/('.env.'+profile)).write_text('\n'.join(k+'='+v for k,v in env.items())+'\n',encoding='utf-8')
        (ROOT/'.runtime'/f'{profile}.identity.json').write_text(json.dumps({'profile':profile,'database':name,'instance_id':instance},indent=2),encoding='utf-8')
        report=ROOT/'docs/reports/v2_0_environments';report.mkdir(parents=True,exist_ok=True)
        (report/f'{profile}_initial_manifest.json').write_text(json.dumps({**manifest,'verification':results},ensure_ascii=False,indent=2),encoding='utf-8')
        target.dispose();print(profile,name,'created; ABC sample matching verified')
    engine.dispose()

def pack(value):
    if value is None:return ['null',None]
    if isinstance(value,bytes):return ['bytes',base64.b64encode(value).decode()]
    if isinstance(value,Decimal):return ['decimal',str(value)]
    if isinstance(value,datetime):return ['datetime',value.isoformat()]
    if isinstance(value,date):return ['date',value.isoformat()]
    return ['json',value]

def unpack(value):
    kind,data=value
    return {'null':lambda:None,'bytes':lambda:base64.b64decode(data),'decimal':lambda:Decimal(data),'datetime':lambda:datetime.fromisoformat(data),'date':lambda:date.fromisoformat(data),'json':lambda:data}[kind]()

def capture_tables(conn):
    tables={}
    for name,table in Base.metadata.tables.items():
        rows=conn.execute(select(table).order_by(table.c.id)).mappings()
        tables[name]=[{k:pack(v) for k,v in r.items()} for r in rows]
    return tables

def write_backup(conn,profile):
    assert_target(conn,profile)
    tables=capture_tables(conn)
    payload={'format':1,'database':NAMES[profile],'profile':profile,'version':'0008','tables':tables}
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8');checksum=hashlib.sha256(raw).hexdigest()
    folder=ROOT/'.backups'/profile;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]+'.json')
    with path.open('xb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    path.with_suffix('.sha256').write_text(checksum,encoding='ascii')
    verified=load_backup(path,profile)
    if verified!=payload:raise RuntimeError('备份回读校验失败，禁止清理')
    return path

def load_backup(path,profile):
    path=Path(path).resolve();folder=(ROOT/'.backups'/profile).resolve()
    if path.parent!=folder:raise RuntimeError('备份不在该环境的专用备份目录')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=path.with_suffix('.sha256').read_text(encoding='ascii').strip():raise RuntimeError('备份校验值不符')
    value=json.loads(raw)
    if value['profile']!=profile or value['database']!=NAMES[profile] or value['version']!='0008' or set(value['tables'])!=set(Base.metadata.tables):raise RuntimeError('备份环境或表结构不符')
    return value

def clear_business(conn):
    name=conn.scalar(text('SELECT DATABASE()'))
    if name not in NAMES.values() and not re.fullmatch(r'order_automation_envcheck_[a-f0-9]{32}',name):raise RuntimeError('禁止清理历史库或未知数据库')
    # No TRUNCATE, no DDL, no FK disabling. All changes can roll back together.
    for name in DELETE_ORDER:
        if name=='sales_order':conn.exec_driver_sql('UPDATE sales_order SET source_order_id=NULL WHERE source_order_id IS NOT NULL')
        if name=='import_task':conn.exec_driver_sql('UPDATE import_task SET retry_of_task_id=NULL WHERE retry_of_task_id IS NOT NULL')
        conn.exec_driver_sql('DELETE FROM `'+name+'`')

def restore_rows(conn,payload):
    clear_business(conn)
    # Insert self-FK links in a second pass. identity must remain the same.
    order=['customer_config','match_rule','configuration_version','import_task','sales_order','import_task_row','manual_correction','order_change','ai_extraction']
    for name in order:
        table=Base.metadata.tables[name]
        for stored in payload['tables'][name]:
            row={k:unpack(v) for k,v in stored.items()}
            if name=='sales_order':row['source_order_id']=None
            if name=='import_task':row['retry_of_task_id']=None
            conn.execute(table.insert().values(**row))
    for name,field in [('import_task','retry_of_task_id'),('sales_order','source_order_id')]:
        table=Base.metadata.tables[name]
        for stored in payload['tables'][name]:
            value=unpack(stored[field])
            if value is not None:conn.execute(table.update().where(table.c.id==unpack(stored['id'])).values(**{field:value,'updated_at':unpack(stored['updated_at'])}))
    # Keep the owned instance identity; restore its baseline label alongside data.
    identity=payload['tables']['environment_identity'][0]
    conn.execute(EnvironmentIdentity.__table__.update().where(EnvironmentIdentity.id==1).values(dataset_version=unpack(identity['dataset_version'])))

def assert_idle(conn):
    own=conn.scalar(text('SELECT CONNECTION_ID()'))
    busy=conn.scalar(text('SELECT count(*) FROM information_schema.processlist WHERE DB=:db AND ID<>:own'),{'db':conn.engine.url.database,'own':own})
    if busy:raise RuntimeError('目标库仍有连接；请关闭该环境应用、SQL 客户端和 pytest 后重试。不杀其他连接。')

def reset(profile,restore=None):
    engine=admin_engine(NAMES[profile]);backup=None
    try:
        with engine.connect() as conn:
            assert_target(conn,profile);assert_idle(conn)
            lock='cvte:maintenance:'+NAMES[profile]
            if conn.scalar(text('SELECT GET_LOCK(:lock,0)'),{'lock':lock})!=1:raise RuntimeError('已有维护操作，停止')
            try:
                assert_idle(conn);backup=write_backup(conn,profile);print('Verified backup:',backup)
                payload=load_backup(restore,profile) if restore else None
                if payload:
                    current=conn.execute(select(EnvironmentIdentity.instance_id).where(EnvironmentIdentity.id==1)).scalar_one()
                    if unpack(payload['tables']['environment_identity'][0]['instance_id'])!=current:raise RuntimeError('备份不属于该库实例')
                conn.commit();assert_idle(conn);conn.commit()
                with conn.begin():
                    assert_target(conn,profile)
                    if payload:restore_rows(conn,payload)
                    else:
                        clear_business(conn)
                        with Session(bind=conn,join_transaction_mode='create_savepoint') as session:
                            seed(session,profile);verify(session);session.commit()
                        if profile=='demo':
                            conn.execute(EnvironmentIdentity.__table__.update().where(EnvironmentIdentity.id==1).values(dataset_version=VERSION))
                print('Restore complete' if payload else 'Reset complete',NAMES[profile])
            finally:
                conn.execute(text('SELECT RELEASE_LOCK(:lock)'),{'lock':lock});conn.commit()
    except Exception:
        if backup:print('Failed: business transaction rolled back. Recovery backup:',backup)
        raise
    finally:engine.dispose()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['initialize','check','plan','reset','restore']);parser.add_argument('--profile',choices=['demo','test']);parser.add_argument('--backup')
    args=parser.parse_args()
    if args.action=='initialize':initialize_all();return
    if not args.profile:raise RuntimeError('必须指定 demo/test')
    if args.action=='restore' and not args.backup:raise RuntimeError('恢复必须提供备份文件')
    if os.environ.get('APP_ENV')!=args.profile:raise RuntimeError('启动环境与请求目标不一致')
    engine=admin_engine(NAMES[args.profile])
    with engine.connect() as conn:assert_target(conn,args.profile)
    engine.dispose()
    if args.action=='check':
        from app.core.database import get_engine
        with get_engine().connect() as conn:assert_target(conn,args.profile)
        print('Verified environment',args.profile,NAMES[args.profile]);return
    print('TARGET:',NAMES[args.profile]);print('CLEAR:',', '.join(DELETE_ORDER));print('History database and other environments are forbidden.')
    if args.profile=='demo' and args.action in {'plan','reset'}:
        print('BASELINE:',VERSION,'| synthetic SEED A=11 B=5 C=8; A/B enabled; C identity only (version 0, no rule/version receipt).')
        print('MATCH: A 9 MATCHED/0 NOT_MATCHED; B two fields 3 MATCHED/1 AMBIGUOUS_MATCH/1 NOT_MATCHED; B three fields 4 MATCHED/1 NOT_MATCHED; C draft >500: 7 MATCHED/1 NOT_MATCHED.')
        print('BACKUP: all ORM tables, typed JSON + SHA256, read-back verified before transactional DELETE; schema and Alembic unchanged.')
    if args.action=='plan':return
    token=('RESTORE ' if args.action=='restore' else 'RESET ')+NAMES[args.profile]
    if input('Type exactly '+token+' to proceed: ')!=token:raise RuntimeError('未明确确认，未执行重置')
    reset(args.profile,args.backup if args.action=='restore' else None)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        # No DB URLs, passwords, SQL parameters or customer data in errors.
        print('Operation stopped:',str(exc) if isinstance(exc,(RuntimeError,ValueError,FileNotFoundError)) else type(exc).__name__,file=sys.stderr);sys.exit(1)
