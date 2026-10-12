import { CapacitorHttp } from '@capacitor/core'
import { Preferences } from '@capacitor/preferences'

const SETTINGS_KEY = 'lazyLibrarianSettings'
const REQUEST_TIMEOUT_MS = 15000

class LazyLibrarianError extends Error {
  constructor(message, code = null) {
    super(message)
    this.name = 'LazyLibrarianError'
    this.code = code
  }
}

class LazyLibrarian {
  constructor() {
    this.settings = { address: '', apiKey: '' }
    this.loaded = false
  }

  /**
   * Accepts what users tend to paste (no scheme, trailing slash, a page URL like /config)
   * and returns the base address the API lives under.
   * @param {string} address
   * @returns {string}
   */
  normalizeAddress(address) {
    let normalized = (address || '').trim()
    if (!normalized) return ''
    if (!/^https?:\/\//i.test(normalized)) normalized = `http://${normalized}`
    normalized = normalized.replace(/\/+$/, '')
    normalized = normalized.replace(/\/(api|config|home|books|audio|authors|series|magazines|manage|history|logs)$/i, '')
    return normalized
  }

  get isConfigured() {
    return !!(this.settings.address && this.settings.apiKey)
  }

  async loadSettings() {
    try {
      const { value } = (await Preferences.get({ key: SETTINGS_KEY })) || {}
      if (value) this.settings = { ...this.settings, ...JSON.parse(value) }
    } catch (error) {
      console.error('[LazyLibrarian] Failed to load settings', error)
    }
    this.loaded = true
    return this.settings
  }

  async getSettings() {
    if (!this.loaded) await this.loadSettings()
    return { ...this.settings }
  }

  async saveSettings({ address, apiKey }) {
    this.settings = {
      address: this.normalizeAddress(address),
      apiKey: (apiKey || '').trim()
    }
    this.loaded = true
    try {
      await Preferences.set({ key: SETTINGS_KEY, value: JSON.stringify(this.settings) })
    } catch (error) {
      console.error('[LazyLibrarian] Failed to save settings', error)
    }
    return { ...this.settings }
  }

  /**
   * Calls the LazyLibrarian API. LL always answers 200 with a text/html content type,
   * so the body is parsed here and `{ Success: false }` payloads are turned into errors.
   * @param {string} cmd
   * @param {Object} [params]
   * @param {{ address: string, apiKey: string }} [settings] override saved settings (used by the connection test)
   */
  async request(cmd, params = {}, settings = null) {
    if (!settings && !this.loaded) await this.loadSettings()
    const address = this.normalizeAddress((settings || this.settings).address)
    const apiKey = ((settings || this.settings).apiKey || '').trim()
    if (!address || !apiKey) {
      throw new LazyLibrarianError('LazyLibrarian is not configured')
    }

    const query = new URLSearchParams({ ...params, cmd, apikey: apiKey })
    console.log(`[LazyLibrarian] Request cmd=${cmd}`)

    let response
    try {
      response = await CapacitorHttp.get({
        url: `${address}/api?${query.toString()}`,
        connectTimeout: REQUEST_TIMEOUT_MS,
        readTimeout: REQUEST_TIMEOUT_MS
      })
    } catch (error) {
      console.error(`[LazyLibrarian] cmd=${cmd} failed`, error)
      throw new LazyLibrarianError(`Unable to reach LazyLibrarian at ${address}`)
    }

    if (response.status >= 400) {
      throw new LazyLibrarianError(`LazyLibrarian returned HTTP ${response.status}`, response.status)
    }

    let data = response.data
    if (typeof data === 'string') {
      try {
        data = JSON.parse(data)
      } catch {
        throw new LazyLibrarianError('Unexpected response from LazyLibrarian (is this the right address?)')
      }
    }

    if (data && typeof data === 'object' && !Array.isArray(data) && data.Success === false) {
      const error = data.Error || {}
      throw new LazyLibrarianError(error.Message || 'LazyLibrarian request failed', error.Code || null)
    }
    return data
  }

  /**
   * @param {{ address: string, apiKey: string }} [settings]
   * @returns {Promise<{ version: string, installType: string }>}
   */
  async testConnection(settings = null) {
    const data = await this.request('getVersion', {}, settings)
    return {
      version: data.current_version || 'unknown',
      installType: data.install_type || ''
    }
  }
}

export default (context, inject) => {
  inject('lazylibrarian', new LazyLibrarian())
}
