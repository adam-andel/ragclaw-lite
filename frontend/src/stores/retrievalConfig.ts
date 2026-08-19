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
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getKbRetrievalConfig, type RetrievalConfig } from '@/api/retrieval'

export const useRetrievalConfigStore = defineStore('retrievalConfig', () => {
  const configCache = ref<Map<string, RetrievalConfig>>(new Map())

  async function getConfig(kbId: string): Promise<RetrievalConfig> {
    if (configCache.value.has(kbId)) {
      return configCache.value.get(kbId)!
    }

    try {
      const response = await getKbRetrievalConfig(kbId)
      const config = response.data
      configCache.value.set(kbId, config)
      return config
    } catch (error) {
      console.error('Failed to fetch retrieval config:', error)
      return {
        vector_weight: null,
        bm25_weight: null,
        vector_top_k: null,
        bm25_top_k: null,
        final_top_k: null,
        similarity_threshold: null
      }
    }
  }

  function updateConfig(kbId: string, config: RetrievalConfig) {
    configCache.value.set(kbId, config)
  }

  function clearConfig(kbId: string) {
    configCache.value.delete(kbId)
  }

  function clearAll() {
    configCache.value.clear()
  }

  return {
    configCache,
    getConfig,
    updateConfig,
    clearConfig,
    clearAll
  }
})
