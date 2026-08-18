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
