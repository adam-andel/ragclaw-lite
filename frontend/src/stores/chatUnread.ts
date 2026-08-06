import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/**
 * Tracks assistant answers that finished streaming while the user was NOT
 * looking at that conversation — so we can surface an "unread answer" hint:
 *  - a red dot on the chat history button (when the user is on another chat),
 *  - a red dot on the sidebar Chat label (when the user is on another page),
 *  - an "有未读消息" badge on the matching conversation in the history modal.
 */
export const useChatUnreadStore = defineStore('chatUnread', () => {
  // Conversation ids that have a generated-but-unread assistant answer.
  const unreadConvIds = ref<string[]>([])
  // The most recently generated unread conversation id (for the sidebar dot /
  // auto-open when returning to the chat page).
  const lastConversationId = ref<string | null>(null)
  // Monotonic event signal: incremented on EVERY markUnread call (even for a
  // conversation that is already marked unread). Consumers that want to fire a
  // notification PER red-dot appearance (not just per new conversation) watch
  // this instead of diffing unreadConvIds, so a 2nd suspension on the same
  // conversation still notifies.
  const unreadEventSeq = ref(0)
  const lastUnreadEvent = ref<{ id: string | null; seq: number }>({ id: null, seq: 0 })

  const hasUnread = computed(() => unreadConvIds.value.length > 0)

  function markUnread(convId: string | null | undefined) {
    if (!convId) return
    const id = convId as string
    if (!unreadConvIds.value.includes(id)) {
      unreadConvIds.value.push(id)
    }
    lastConversationId.value = id
    unreadEventSeq.value += 1
    lastUnreadEvent.value = { id, seq: unreadEventSeq.value }
  }

  /** Fire a browser notification for this conversation WITHOUT adding it to
   *  unreadConvIds (no red dot). Use when the user is actively looking at the
   *  conversation — they see the result inline, but should still get a desktop
   *  notification in case they tabbed away. */
  function notifyOnly(convId: string | null | undefined) {
    if (!convId) return
    const id = convId as string
    unreadEventSeq.value += 1
    lastUnreadEvent.value = { id, seq: unreadEventSeq.value }
  }

  // Clear a single conversation's unread flag (e.g. when it is opened).
  function clearConversation(convId: string | null | undefined) {
    if (!convId) return
    const id = convId as string
    const idx = unreadConvIds.value.indexOf(id)
    if (idx !== -1) unreadConvIds.value.splice(idx, 1)
    if (lastConversationId.value === id) {
      lastConversationId.value = unreadConvIds.value.length
        ? unreadConvIds.value[unreadConvIds.value.length - 1]
        : null
    }
  }

  function clearUnread() {
    unreadConvIds.value = []
    lastConversationId.value = null
  }

  function hasUnreadConversation(convId: string | null | undefined) {
    if (!convId) return false
    return unreadConvIds.value.includes(convId as string)
  }

  function reset() {
    clearUnread()
  }

  return {
    unreadConvIds,
    lastConversationId,
    unreadEventSeq,
    lastUnreadEvent,
    hasUnread,
    markUnread,
    notifyOnly,
    clearConversation,
    clearUnread,
    hasUnreadConversation,
    reset,
  }
})
