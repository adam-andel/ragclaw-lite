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

  const hasUnread = computed(() => unreadConvIds.value.length > 0)

  function markUnread(convId: string | null | undefined) {
    if (!convId) return
    const id = convId as string
    if (!unreadConvIds.value.includes(id)) {
      unreadConvIds.value.push(id)
    }
    lastConversationId.value = id
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
    hasUnread,
    markUnread,
    clearConversation,
    clearUnread,
    hasUnreadConversation,
    reset,
  }
})
