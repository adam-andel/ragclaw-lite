import { ref } from 'vue'

/**
 * 浏览器桌面通知封装（Web Notifications API）。
 *
 * 设计原则：
 * - 仅当用户“事先”授予权限（Notification.permission === 'granted'）时才发送，
 *   绝不在轮询 / 后台逻辑中主动弹出权限请求。
 * - 申请权限必须由用户手势触发（按钮点击等），调用方应在合适处调用 requestPermission()。
 * - permission 为模块级单例响应式状态，所有组件共享同一份，避免状态不一致。
 */

type NotifyPermission = 'granted' | 'denied' | 'default' | 'unsupported'

function isSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window
}

const supported = isSupported()

const permission = ref<NotifyPermission>(
  supported ? (Notification.permission as NotifyPermission) : 'unsupported',
)

function syncPermission() {
  if (supported) permission.value = Notification.permission as NotifyPermission
}

export function useBrowserNotification() {
  async function requestPermission(): Promise<NotifyPermission> {
    if (!supported) return 'unsupported'
    // 已是 granted / denied 则无需再请求（denied 只能由用户在浏览器设置中更改）
    if (Notification.permission !== 'default') {
      syncPermission()
      return permission.value
    }
    try {
      const result = await Notification.requestPermission()
      permission.value = result
      return result
    } catch (e) {
      // 兼容旧版回调式 API 抛错的情况
      console.error('[BrowserNotification] requestPermission failed', e)
      return permission.value
    }
  }

  function notify(title: string, options: NotificationOptions = {}): Notification | null {
    if (!supported) return null
    if (Notification.permission !== 'granted') return null
    try {
      return new Notification(title, options)
    } catch (e) {
      console.error('[BrowserNotification] notify failed', e)
      return null
    }
  }

  return {
    supported,
    permission,
    requestPermission,
    notify,
    isGranted: () => permission.value === 'granted',
  }
}
