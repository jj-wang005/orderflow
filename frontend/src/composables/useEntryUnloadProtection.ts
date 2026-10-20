import { onBeforeUnmount, onMounted } from 'vue'

// SPA navigation retains the cached workbench; reload/closing would lose File objects
// and unsaved edits. Existing persisted request keys remain the recovery authority.
export function useEntryUnloadProtection(hasUnsavedWork: () => boolean) {
  function protect(event: BeforeUnloadEvent) {
    if (!hasUnsavedWork()) return
    event.preventDefault()
    event.returnValue = ''
  }
  onMounted(() => window.addEventListener('beforeunload', protect))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', protect))
}
