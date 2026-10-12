<template>
  <div class="w-full h-full overflow-y-auto pb-20">
    <div class="px-4 pt-4 pb-1">
      <ui-text-input v-model="searchQuery" borderless :placeholder="$strings.PlaceholderSearchBooks" bg="white bg-opacity-10" rounded="md" prepend-icon="search" text-size="base" clearable class="w-full" @input="searchInput" />
    </div>

    <template v-if="isSearching">
      <div v-if="searchLoading" class="flex justify-center py-10">
        <ui-loading-indicator />
      </div>
      <p v-else-if="searchError" class="text-sm text-fg-muted text-center px-4 py-10">{{ searchError }}</p>
      <p v-else-if="!searchResults.length" class="text-sm text-fg-muted text-center py-10">{{ $strings.MessageNoItemsFound }}</p>
      <div v-else class="grid gap-x-3 gap-y-4 px-4 py-3" :style="{ gridTemplateColumns: `repeat(auto-fill, ${cardWidth}px)`, justifyContent: 'space-between' }">
        <discover-book-card v-for="book in searchResults" :key="book.id" :book="book" :width="cardWidth" @click="selectBook" />
      </div>
    </template>

    <template v-else>
      <discover-row v-for="row in visibleRows" :key="row.id" :label="row.label" :books="row.books" :loading="row.loading" :error="row.error" :card-width="cardWidth" @select="selectBook" />
    </template>

    <!-- Modal.vue detaches its own element on mount, so keep it in a wrapper that stays put; otherwise
         Vue loses its anchor when inserting the rows/search results above it and renders nothing -->
    <div>
      <discover-book-modal v-model="showBookModal" :book="selectedBook" />
    </div>
  </div>
</template>

<script>
const SEARCH_DEBOUNCE_MS = 500

export default {
  data() {
    return {
      rows: [],
      searchQuery: '',
      searchResults: [],
      searchLoading: false,
      searchError: null,
      searchTimeout: null,
      searchRequestId: 0,
      cardWidth: 100,
      selectedBook: null,
      showBookModal: false
    }
  },
  computed: {
    visibleRows() {
      // Open Library tags books loosely (Watchmen is sci-fi, fantasy and mystery), so show each book only in its first row
      const seen = new Set()
      return this.rows.map((row) => {
        const books = row.books.filter((book) => !seen.has(book.id))
        books.forEach((book) => seen.add(book.id))
        return { ...row, books }
      })
    },
    isSearching() {
      return !!(this.searchQuery || '').trim()
    },
    rowDefinitions() {
      return [
        { id: 'trending', label: this.$strings.HeaderTrendingThisWeek, load: () => this.$openlibrary.getTrending('weekly') },
        { id: 'scifi', label: this.$strings.LabelGenreScienceFiction, load: () => this.$openlibrary.getSubject('science fiction') },
        { id: 'fantasy', label: this.$strings.LabelGenreFantasy, load: () => this.$openlibrary.getSubject('epic fantasy') },
        { id: 'mystery', label: this.$strings.LabelGenreMystery, load: () => this.$openlibrary.getSubject('mystery and detective stories') },
        { id: 'thriller', label: this.$strings.LabelGenreThriller, load: () => this.$openlibrary.getSubject('suspense fiction') },
        { id: 'romance', label: this.$strings.LabelGenreRomance, load: () => this.$openlibrary.getSubject('romance fiction') },
        { id: 'biography', label: this.$strings.LabelGenreBiography, load: () => this.$openlibrary.getSubject('biography & autobiography') },
        { id: 'history', label: this.$strings.LabelGenreHistory, load: () => this.$openlibrary.getSubject('world history') }
      ]
    }
  },
  methods: {
    selectBook(book) {
      this.selectedBook = book
      this.showBookModal = true
    },
    async loadRow(row, definition) {
      try {
        row.books = await definition.load()
        if (!row.books.length) row.error = this.$strings.MessageNoItemsFound
      } catch (error) {
        console.error(`[Discover] Failed to load row ${row.id}`, error)
        row.error = this.$strings.MessageDiscoverLoadFailed
      }
      row.loading = false
    },
    loadRows() {
      this.rows = this.rowDefinitions.map((def) => ({ id: def.id, label: def.label, books: [], loading: true, error: null }))
      // Open Library is slow under load; fire rows in parallel so each fills in as it arrives
      this.rows.forEach((row, index) => this.loadRow(row, this.rowDefinitions[index]))
    },
    searchInput() {
      clearTimeout(this.searchTimeout)
      if (!this.isSearching) {
        this.searchResults = []
        this.searchLoading = false
        this.searchError = null
        return
      }
      this.searchLoading = true
      this.searchTimeout = setTimeout(this.runSearch, SEARCH_DEBOUNCE_MS)
    },
    async runSearch() {
      const requestId = ++this.searchRequestId
      const query = this.searchQuery.trim()
      this.searchError = null
      try {
        const results = await this.$openlibrary.search(query)
        if (requestId !== this.searchRequestId) return
        this.searchResults = results
      } catch (error) {
        if (requestId !== this.searchRequestId) return
        console.error('[Discover] Search failed', error)
        this.searchResults = []
        this.searchError = this.$strings.MessageDiscoverLoadFailed
      }
      this.searchLoading = false
    }
  },
  mounted() {
    // Fit three cards per screen width with room for the next one to peek in
    this.cardWidth = Math.min(140, Math.max(90, Math.floor((window.innerWidth - 32) / 3.4)))
    this.loadRows()
  },
  beforeDestroy() {
    clearTimeout(this.searchTimeout)
  }
}
</script>
