import { CapacitorHttp } from '@capacitor/core'

const BASE_URL = 'https://openlibrary.org'
const COVERS_URL = 'https://covers.openlibrary.org/b/id'
// editions.* returns the edition that best matches the query, used to show English titles/covers for translated works
const SEARCH_FIELDS = 'key,title,author_name,cover_i,first_publish_year,editions,editions.title,editions.language,editions.cover_i'
const REQUEST_TIMEOUT_MS = 20000
const CACHE_TTL_MS = 30 * 60 * 1000

/**
 * @typedef {Object} DiscoverBook
 * @property {string} id Open Library work id (e.g. OL21745884W), the same id LazyLibrarian uses
 * @property {string} title
 * @property {string} author
 * @property {number|null} year
 * @property {string|null} coverUrl
 */

class OpenLibrary {
  constructor() {
    this.cache = new Map()
  }

  coverUrl(coverId, size = 'M') {
    return coverId ? `${COVERS_URL}/${coverId}-${size}.jpg` : null
  }

  /**
   * Trending and search results use slightly different field names for the same data
   * @returns {DiscoverBook|null}
   */
  toBook(work) {
    const id = (work.key || '').replace('/works/', '')
    if (!id || !work.title) return null

    // Works are titled in their original language ("Män som hatar kvinnor"), so prefer the English edition
    const edition = work.editions?.docs?.[0]
    const englishEdition = edition?.title && (edition.language || []).includes('eng') ? edition : null
    let title = work.title
    let coverId = work.cover_i
    if (englishEdition && englishEdition.title.toLowerCase() !== work.title.toLowerCase()) {
      title = englishEdition.title === englishEdition.title.toLowerCase() ? this.titleCase(englishEdition.title) : englishEdition.title
      // Only swap covers for translated works; for English works the main cover is usually the nicest one
      coverId = englishEdition.cover_i || coverId
    }

    return {
      id,
      title,
      author: (work.author_name || []).slice(0, 2).join(', '),
      year: work.first_publish_year || null,
      coverUrl: this.coverUrl(coverId)
    }
  }

  titleCase(text) {
    return text.replace(/(^|\s)(\S)/g, (match, space, char) => space + char.toUpperCase())
  }

  async get(path, params = {}) {
    const url = `${BASE_URL}${path}?${new URLSearchParams(params).toString()}`
    const cached = this.cache.get(url)
    if (cached && Date.now() - cached.time < CACHE_TTL_MS) return cached.data

    const response = await CapacitorHttp.get({ url, connectTimeout: REQUEST_TIMEOUT_MS, readTimeout: REQUEST_TIMEOUT_MS })
    if (response.status >= 400) {
      throw new Error(`Open Library returned HTTP ${response.status}`)
    }
    const data = typeof response.data === 'string' ? JSON.parse(response.data) : response.data
    this.cache.set(url, { time: Date.now(), data })
    return data
  }

  /**
   * @param {'daily'|'weekly'|'monthly'} period
   * @returns {Promise<DiscoverBook[]>}
   */
  async getTrending(period = 'weekly', limit = 20) {
    const data = await this.get(`/trending/${period}.json`, { limit })
    return (data.works || []).map((w) => this.toBook(w)).filter(Boolean)
  }

  /**
   * Highest rated English works for a subject. The /subjects endpoint sorts by edition count,
   * which only surfaces old classics, so this goes through search instead.
   * @returns {Promise<DiscoverBook[]>}
   */
  async getSubject(subject, limit = 20) {
    const data = await this.get('/search.json', {
      q: `subject:"${subject}" language:eng`,
      sort: 'rating',
      fields: SEARCH_FIELDS,
      lang: 'en',
      limit
    })
    return (data.docs || []).map((w) => this.toBook(w)).filter(Boolean)
  }

  /**
   * Description for the book details sheet. Open Library descriptions are either a string or
   * { type, value } and often end with a "----------" block of source links, which is dropped.
   * @returns {Promise<{ description: string }>}
   */
  async getWork(id) {
    const data = await this.get(`/works/${id}.json`)
    let description = data.description?.value ?? data.description ?? ''
    if (typeof description !== 'string') description = ''
    description = description.split(/\r?\n-{3,}/)[0].replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').trim()
    return { description }
  }

  /**
   * @returns {Promise<DiscoverBook[]>}
   */
  async search(query, limit = 30) {
    const data = await this.get('/search.json', { q: query, fields: SEARCH_FIELDS, lang: 'en', limit })
    const books = (data.docs || []).map((w) => this.toBook(w)).filter(Boolean)
    // Coverless results are mostly self-published filler (coloring books, notebooks); keep relevance order otherwise
    return [...books.filter((b) => b.coverUrl), ...books.filter((b) => !b.coverUrl)]
  }
}

export default (context, inject) => {
  inject('openlibrary', new OpenLibrary())
}
