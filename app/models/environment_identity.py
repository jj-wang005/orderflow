from sqlalchemy import Integer,String
from sqlalchemy.orm import Mapped,mapped_column
from app.models.base import Base

class EnvironmentIdentity(Base):
    __tablename__='environment_identity'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    profile:Mapped[str]=mapped_column(String(16))
    instance_id:Mapped[str]=mapped_column(String(36),unique=True)
    dataset_version:Mapped[str]=mapped_column(String(64))
