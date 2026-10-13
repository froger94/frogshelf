import { CapacitorHttp } from '@capacitor/core'

// The Frogshelf broker runs next to Audiobookshelf, so its address follows from the server address: on this port for
// LAN/Tailscale addresses, or under this path when Audiobookshelf is reached through a domain (Cloudflare Tunnel)
const BROKER_PORT = 30091
const BROKER_PATH = '/frogshelf'
const IP_ADDRESS = /^(\d{1,3}(\.\d{1,3}){3}|\[[0-9a-f:]+\])$/i
const REQUEST_TIMEOUT_MS = 20000
// Approving a request or swapping a release waits for LazyLibrarian to search every indexer
const SLOW_TIMEOUT_MS = 150000

class BrokerError extends Error {
  constructor(message, status = null) {
    super(message)
    this.name = 'BrokerError'
    this.status = status
  }
}

class Broker {
  constructor(context) {
    this.context = context
    this.store = context.store
  }

  /**
   * @param {string} serverAddress Audiobookshelf address, e.g. http://192.168.1.10:13378
   * @returns {string|null}
   */
  urlFor(serverAddress) {
    try {
      const url = new URL(serverAddress)
      if (url.port || IP_ADDRESS.test(url.hostname) || url.hostname === 'localhost') return `http://${url.hostname}:${BROKER_PORT}`
      return `${url.origin}${BROKER_PATH}`
    } catch {
      return null
    }
  }

  get baseUrl() {
    return this.urlFor(this.store.getters['user/getServerAddress'])
  }

  /** Covers from LazyLibrarian come back as broker paths; Open Library covers are already absolute */
  coverUrl(path) {
    if (!path) return null
    return path.startsWith('/') ? `${this.baseUrl}${path}` : path
  }

  async send(method, baseUrl, path, { body = null, auth = true, timeout = REQUEST_TIMEOUT_MS, retried = false } = {}) {
    if (!baseUrl) throw new BrokerError('Not connected to an Audiobookshelf server')
    const headers = {}
    if (body) headers['Content-Type'] = 'application/json'
    if (auth) {
      const token = this.store.getters['user/getToken']
      if (!token) throw new BrokerError('Sign in to Audiobookshelf first', 401)
      headers.Authorization = `Bearer ${token}`
    }

    let response
    try {
      response = await CapacitorHttp.request({ method, url: `${baseUrl}${path}`, headers, data: body, connectTimeout: REQUEST_TIMEOUT_MS, readTimeout: timeout })
    } catch (error) {
      console.error(`[Broker] ${method} ${path} failed`, error)
      throw new BrokerError("Can't reach the request server. Away from home? Check that Tailscale is on.")
    }

    if (response.status === 401 && auth && !retried) {
      // The Audiobookshelf access token may have expired; any Audiobookshelf call through nativeHttp refreshes it
      await this.context.$nativeHttp.get('/api/me').catch(() => null)
      return this.send(method, baseUrl, path, { body, auth, timeout, retried: true })
    }

    let data = response.data
    if (typeof data === 'string') {
      try {
        data = JSON.parse(data)
      } catch {
        data = { error: data }
      }
    }
    if (response.status >= 400) {
      throw new BrokerError(data?.error || `Request server returned HTTP ${response.status}`, response.status)
    }
    return data
  }

  call(method, path, options = {}) {
    return this.send(method, this.baseUrl, path, options)
  }

  // -- signed-in users --

  /** @returns {Promise<Object<string, 'pending'|'requested'|'downloading'|'library'>>} */
  async getStatuses() {
    const { statuses } = await this.call('GET', '/api/books/status')
    return statuses || {}
  }

  /**
   * @param {{ id: string, title: string, author: string, coverUrl: string|null }} book Discover book
   * @returns {Promise<'pending'|'requested'|'downloading'|'library'>}
   */
  async requestBook(book) {
    const { status } = await this.call('POST', '/api/requests', {
      body: { bookId: book.id, title: book.title, author: book.author, coverUrl: book.coverUrl },
      timeout: SLOW_TIMEOUT_MS
    })
    return status
  }

  getRequests() {
    return this.call('GET', '/api/requests')
  }

  cancelRequest(id) {
    return this.call('POST', `/api/requests/${id}/cancel`)
  }

  // -- admins --

  approveRequest(id) {
    return this.call('POST', `/api/requests/${id}/approve`, { timeout: SLOW_TIMEOUT_MS })
  }

  denyRequest(id) {
    return this.call('POST', `/api/requests/${id}/deny`)
  }

  approveSignup(id) {
    return this.call('POST', `/api/signups/${id}/approve`)
  }

  denySignup(id) {
    return this.call('POST', `/api/signups/${id}/deny`)
  }

  searchAgain(bookId) {
    return this.call('POST', `/api/books/${bookId}/search`)
  }

  cancelBook(bookId) {
    return this.call('POST', `/api/books/${bookId}/cancel`)
  }

  /** @returns {Promise<'new'|'same'|'none'>} */
  async tryAnotherRelease(bookId) {
    const { outcome } = await this.call('POST', `/api/books/${bookId}/try-another-release`, { timeout: SLOW_TIMEOUT_MS })
    return outcome
  }

  // -- account requests (before signing in, so addressed by server address and unauthenticated) --

  /** @returns {Promise<{ signup: boolean }|null>} null when the server has no broker */
  async getInfo(serverAddress) {
    try {
      const info = await this.send('GET', this.urlFor(serverAddress), '/api/info', { auth: false, timeout: 5000 })
      return info?.app === 'frogshelf-broker' ? info : null
    } catch {
      return null
    }
  }

  requestAccount(serverAddress, { username, displayName, note }) {
    return this.send('POST', this.urlFor(serverAddress), '/api/signup', { auth: false, body: { username, displayName, note } })
  }

  getAccountRequest(serverAddress, id, token) {
    return this.send('GET', this.urlFor(serverAddress), `/api/signup/${id}?token=${encodeURIComponent(token)}`, { auth: false })
  }

  completeAccountRequest(serverAddress, id, token, password) {
    return this.send('POST', this.urlFor(serverAddress), `/api/signup/${id}/complete`, { auth: false, body: { token, password } })
  }
}

export default (context, inject) => {
  inject('broker', new Broker(context))
}
