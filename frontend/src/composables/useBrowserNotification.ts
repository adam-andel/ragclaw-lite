// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { ref } from 'vue'

/**
 * Wrapper for browser desktop notifications (Web Notifications API).
 *
 * Design principles:
 * - Only send a notification when the user has ALREADY granted permission (Notification.permission === 'granted');
 *   never proactively pop up a permission request during polling or background logic.
 * - Requesting permission must be triggered by a user gesture (e.g. a button click); callers should call
 *   requestPermission() at an appropriate place.
 * - permission is a module-level singleton reactive state shared by all components to avoid inconsistency.
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
    // If already granted / denied, no need to request again (denied can only be changed by the user in the browser settings)
    if (Notification.permission !== 'default') {
      syncPermission()
      return permission.value
    }
    try {
      const result = await Notification.requestPermission()
      permission.value = result
      return result
    } catch (e) {
      // Compatibility for the old callback-style API throwing errors
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
