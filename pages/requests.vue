<template>
  <div class="w-full h-full py-6 px-4 overflow-y-auto">
    <div class="flex items-center mb-4">
      <p class="text-base text-fg flex-grow">{{ $strings.HeaderRequests }}</p>
      <ui-icon-btn icon="refresh" borderless :loading="loading" @click="load" />
    </div>

    <template v-if="!llConfigured">
      <p class="text-sm text-fg-muted">{{ $strings.MessageDiscoverSetupLazyLibrarian }}</p>
      <ui-btn small color="primary" class="mt-2" to="/settings">{{ $strings.HeaderSettings }}</ui-btn>
    </template>

    <p v-else-if="error" class="text-sm text-error">{{ error }}</p>

    <div v-else-if="!loading && !sections.length" class="text-center py-10">
      <p class="text-sm text-fg-muted">{{ $strings.MessageNoRequests }}</p>
      <ui-btn small color="primary" class="mt-4" to="/bookshelf/discover">{{ $strings.ButtonDiscover }}</ui-btn>
    </div>

    <template v-for="section in sections">
      <p :key="`${section.id}-header`" class="uppercase text-xs font-semibold text-fg-muted mt-6 mb-2">{{ section.label }}</p>
      <div v-for="book in section.books" :key="book.id" class="flex items-center py-2" @click="openBook(book)">
        <div class="w-12 h-[72px] flex-none rounded-sm overflow-hidden bg-primary">
          <img v-if="book.coverUrl" :src="book.coverUrl" class="w-full h-full object-cover" />
        </div>
        <div class="px-3 flex-grow min-w-0">
          <p class="text-sm truncate">{{ book.title }}</p>
          <p class="text-xs text-fg-muted truncate">{{ book.author }}</p>

          <template v-if="book.status === 'downloading'">
            <div class="w-full h-1.5 rounded-full bg-primary mt-2 overflow-hidden">
              <div class="h-full bg-success transition-all" :style="{ width: (progress[book.id] || 0) + '%' }" />
            </div>
            <p class="text-xs text-fg-muted pt-1">{{ progress[book.id] || 0 }}%</p>
          </template>
          <p v-else-if="book.status === 'library' && book.libraryDate" class="text-xs text-fg-muted pt-1">{{ $getString('LabelAddedDate', [formatDate(book.libraryDate)]) }}</p>
        </div>

        <div v-if="book.status === 'requested'" class="flex flex-none" @click.stop>
          <ui-icon-btn icon="search" borderless :loading="busyId === book.id" @click="searchAgain(book)" />
          <ui-icon-btn icon="close" borderless :disabled="busyId === book.id" @click="cancel(book)" />
        </div>
      </div>
    </template>

    <div>
      <discover-book-modal v-model="showBookModal" :book="selectedBook" />
    </div>
  </div>
</template>

<script>
import { Dialog } from '@capacitor/dialog'

const REFRESH_INTERVAL_MS = 10000
const RECENT_DAYS = 30

export default {
  data() {
    return {
      loading: false,
      llConfigured: true,
      error: null,
      requests: [],
      progress: {},
      busyId: null,
      refreshInterval: null,
      selectedBook: null,
      showBookModal: false
    }
  },
  computed: {
    sections() {
      const recentCutoff = Date.now() - RECENT_DAYS * 24 * 60 * 60 * 1000
      const sections = [
        { id: 'downloading', label: this.$strings.LabelDiscoverDownloading, books: this.requests.filter((b) => b.status === 'downloading') },
        { id: 'requested', label: this.$strings.HeaderWanted, books: this.requests.filter((b) => b.status === 'requested') },
        {
          id: 'library',
          label: this.$strings.HeaderRecentlyAdded,
          books: this.requests
            .filter((b) => b.status === 'library' && b.libraryDate && new Date(b.libraryDate).getTime() > recentCutoff)
            .sort((a, b) => new Date(b.libraryDate) - new Date(a.libraryDate))
        }
      ]
      return sections.filter((section) => section.books.length)
    }
  },
  methods: {
    formatDate(date) {
      return this.$formatDate(new Date(date).getTime(), 'MMM d')
    },
    openBook(book) {
      this.selectedBook = { id: book.id, title: book.title, author: book.author, year: null, coverUrl: book.coverUrl }
      this.showBookModal = true
    },
    async load() {
      await this.$lazylibrarian.getSettings()
      this.llConfigured = this.$lazylibrarian.isConfigured
      if (!this.llConfigured) return

      this.loading = true
      try {
        this.requests = await this.$lazylibrarian.getAudiobookRequests()
        if (this.requests.some((b) => b.status === 'downloading')) {
          this.progress = await this.$lazylibrarian.getDownloadProgress()
        }
        this.error = null
      } catch (error) {
        console.error('[Requests] Failed to load', error)
        this.error = error.message
      }
      this.loading = false
    },
    async searchAgain(book) {
      this.busyId = book.id
      try {
        await this.$lazylibrarian.searchAudiobook(book.id)
        this.$toast.success(this.$strings.ToastRequestSearchStarted)
        // The search runs in the background; a hit shows up as downloading on the next refresh
        setTimeout(this.load, 5000)
      } catch (error) {
        this.$toast.error(error.message)
      }
      this.busyId = null
    },
    async cancel(book) {
      const { value } = await Dialog.confirm({ title: book.title, message: this.$strings.MessageConfirmCancelRequest })
      if (!value) return
      this.busyId = book.id
      try {
        await this.$lazylibrarian.cancelAudiobook(book.id)
        this.requests = this.requests.filter((b) => b.id !== book.id)
      } catch (error) {
        this.$toast.error(error.message)
      }
      this.busyId = null
    }
  },
  mounted() {
    this.load()
    this.refreshInterval = setInterval(() => {
      if (!this.loading && !this.showBookModal && this.llConfigured) this.load()
    }, REFRESH_INTERVAL_MS)
  },
  beforeDestroy() {
    clearInterval(this.refreshInterval)
  }
}
</script>
