<template>
  <div class="w-full h-full py-6 px-4 overflow-y-auto">
    <div class="flex items-center mb-4">
      <p class="text-base text-fg flex-grow">{{ $strings.HeaderRequests }}</p>
      <ui-icon-btn icon="refresh" borderless :loading="loading" @click="load" />
    </div>

    <p v-if="error" class="text-sm text-error">{{ error }}</p>

    <div v-else-if="loaded && !hasAnything" class="text-center py-10">
      <p class="text-sm text-fg-muted">{{ $strings.MessageNoRequests }}</p>
      <ui-btn small color="primary" class="mt-4" to="/bookshelf/discover">{{ $strings.ButtonDiscover }}</ui-btn>
    </div>

    <!-- Admin: account requests -->
    <template v-if="signups.length">
      <p class="uppercase text-xs font-semibold text-fg-muted mt-6 mb-2">{{ $strings.HeaderAccountRequests }}</p>
      <div v-for="signup in signups" :key="`signup-${signup.id}`" class="flex items-center py-2">
        <div class="w-12 h-12 flex-none rounded-full bg-primary flex items-center justify-center">
          <span class="material-symbols text-2xl text-fg-muted">person</span>
        </div>
        <div class="px-3 flex-grow min-w-0">
          <p class="text-sm truncate">{{ signup.displayName || signup.username }}</p>
          <p class="text-xs text-fg-muted truncate">@{{ signup.username }} · {{ timeAgo(signup.createdAt) }}</p>
          <p v-if="signup.note" class="text-xs text-fg-muted pt-0.5 line-clamp-2">“{{ signup.note }}”</p>
        </div>
        <div class="flex flex-none">
          <ui-icon-btn icon="check" borderless :loading="busyId === `signup-${signup.id}`" @click="approveSignup(signup)" />
          <ui-icon-btn icon="close" borderless :disabled="busyId === `signup-${signup.id}`" @click="denySignup(signup)" />
        </div>
      </div>
    </template>

    <!-- Admin: book requests waiting for approval -->
    <template v-if="pending.length">
      <p class="uppercase text-xs font-semibold text-fg-muted mt-6 mb-2">{{ $strings.HeaderBookRequests }}</p>
      <div v-for="request in pending" :key="`pending-${request.id}`" class="flex items-center py-2" @click="openBook(request)">
        <div class="w-12 h-[72px] flex-none rounded-sm overflow-hidden bg-primary">
          <img v-if="request.coverUrl" :src="request.coverUrl" class="w-full h-full object-cover" />
        </div>
        <div class="px-3 flex-grow min-w-0">
          <p class="text-sm truncate">{{ request.title }}</p>
          <p class="text-xs text-fg-muted truncate">{{ request.author }}</p>
          <p class="text-xs text-fg-muted pt-1">{{ $getString('LabelRequestedBy', [request.username]) }} · {{ timeAgo(request.createdAt) }}</p>
        </div>
        <div class="flex flex-none" @click.stop>
          <ui-icon-btn icon="check" borderless :loading="busyId === `request-${request.id}`" @click="approveRequest(request)" />
          <ui-icon-btn icon="close" borderless :disabled="busyId === `request-${request.id}`" @click="denyRequest(request)" />
        </div>
      </div>
    </template>

    <template v-for="section in sections">
      <p :key="`${section.id}-header`" class="uppercase text-xs font-semibold text-fg-muted mt-6 mb-2">{{ section.label }}</p>
      <div v-for="book in section.books" :key="`${section.id}-${book.key}`" class="flex items-center py-2" @click="openBook(book)">
        <div class="w-12 h-[72px] flex-none rounded-sm overflow-hidden bg-primary">
          <img v-if="book.coverUrl" :src="book.coverUrl" class="w-full h-full object-cover" />
        </div>
        <div class="px-3 flex-grow min-w-0">
          <p class="text-sm truncate">{{ book.title }}</p>
          <p class="text-xs text-fg-muted truncate">{{ book.author }}</p>

          <template v-if="book.state === 'downloading'">
            <div class="w-full h-1.5 rounded-full bg-primary mt-2 overflow-hidden">
              <div class="h-full bg-success transition-all" :style="{ width: (book.progress || 0) + '%' }" />
            </div>
            <p class="text-xs text-fg-muted pt-1">{{ book.progress || 0 }}%</p>
          </template>
          <p v-else-if="book.state === 'library' && book.libraryDate" class="text-xs text-fg-muted pt-1">{{ $getString('LabelAddedDate', [formatDate(book.libraryDate)]) }}</p>
          <p v-if="isAdmin && book.requestedBy && book.requestedBy !== username" class="text-xs text-fg-muted pt-0.5">{{ $getString('LabelRequestedBy', [book.requestedBy]) }}</p>
        </div>

        <div v-if="isAdmin && book.state === 'downloading'" class="flex flex-none" @click.stop>
          <ui-icon-btn icon="swap_horiz" borderless :loading="busyId === book.key" @click="tryAnotherRelease(book)" />
        </div>
        <div v-else-if="isAdmin && book.state === 'requested'" class="flex flex-none" @click.stop>
          <ui-icon-btn icon="search" borderless :loading="busyId === book.key" @click="searchAgain(book)" />
          <ui-icon-btn icon="close" borderless :disabled="busyId === book.key" @click="cancelBook(book)" />
        </div>
        <div v-else-if="!isAdmin && book.state === 'pending'" class="flex flex-none" @click.stop>
          <ui-icon-btn icon="close" borderless :disabled="busyId === book.key" @click="cancelRequest(book)" />
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
      loaded: false,
      error: null,
      isAdmin: false,
      signups: [],
      pending: [],
      books: [],
      busyId: null,
      refreshInterval: null,
      selectedBook: null,
      showBookModal: false
    }
  },
  computed: {
    username() {
      return this.$store.state.user.user?.username
    },
    hasAnything() {
      return this.signups.length || this.pending.length || this.sections.length
    },
    sections() {
      const recentCutoff = Date.now() - RECENT_DAYS * 24 * 60 * 60 * 1000
      const byState = (state) => this.books.filter((b) => b.state === state)
      const sections = [
        { id: 'waiting', label: this.$strings.HeaderWaitingForApproval, books: byState('pending') },
        { id: 'downloading', label: this.$strings.LabelDiscoverDownloading, books: byState('downloading') },
        { id: 'requested', label: this.isAdmin ? this.$strings.HeaderWanted : this.$strings.HeaderRequestedBooks, books: byState('requested') },
        {
          id: 'library',
          label: this.isAdmin ? this.$strings.HeaderRecentlyAdded : this.$strings.LabelDiscoverInLibrary,
          books: byState('library')
            .filter((b) => !this.isAdmin || (b.libraryDate && new Date(b.libraryDate).getTime() > recentCutoff))
            .sort((a, b) => new Date(b.libraryDate || 0) - new Date(a.libraryDate || 0))
        },
        { id: 'denied', label: this.$strings.HeaderNotApproved, books: byState('denied') }
      ]
      return sections.filter((section) => section.books.length)
    }
  },
  methods: {
    formatDate(date) {
      return this.$formatDate(new Date(date).getTime(), 'MMM d')
    },
    timeAgo(ms) {
      const minutes = Math.round((Date.now() - ms) / 60000)
      if (minutes < 60) return this.$getString('LabelMinutesAgo', [Math.max(1, minutes)])
      const hours = Math.round(minutes / 60)
      if (hours < 48) return this.$getString('LabelHoursAgo', [hours])
      return this.formatDate(ms)
    },
    openBook(book) {
      this.selectedBook = { id: book.bookId || book.id, title: book.title, author: book.author, year: null, coverUrl: book.coverUrl }
      this.showBookModal = true
    },
    async load() {
      this.loading = true
      try {
        const data = await this.$broker.getRequests()
        this.isAdmin = data.isAdmin
        if (data.isAdmin) {
          this.signups = data.signups || []
          this.pending = (data.pending || []).map((r) => ({ ...r, coverUrl: this.$broker.coverUrl(r.coverUrl) }))
          this.books = (data.books || []).map((b) => ({ ...b, key: b.id, state: b.status, coverUrl: this.$broker.coverUrl(b.coverUrl) }))
        } else {
          this.signups = []
          this.pending = []
          this.books = (data.requests || [])
            .filter((r) => r.state !== 'cancelled')
            .map((r) => ({ ...r, key: `request-${r.id}`, coverUrl: this.$broker.coverUrl(r.coverUrl) }))
        }
        this.error = null
      } catch (error) {
        console.error('[Requests] Failed to load', error)
        this.error = error.message
      }
      this.loading = false
      this.loaded = true
    },
    async run(busyId, action) {
      this.busyId = busyId
      try {
        await action()
      } catch (error) {
        console.error('[Requests] Action failed', error)
        this.$toast.error(error.message)
      }
      this.busyId = null
    },
    async confirm(title, message) {
      const { value } = await Dialog.confirm({ title, message })
      return value
    },
    approveSignup(signup) {
      return this.run(`signup-${signup.id}`, async () => {
        await this.$broker.approveSignup(signup.id)
        this.$toast.success(this.$getString('ToastAccountApproved', [signup.username]))
        this.signups = this.signups.filter((s) => s.id !== signup.id)
      })
    },
    denySignup(signup) {
      return this.run(`signup-${signup.id}`, async () => {
        if (!(await this.confirm(signup.username, this.$strings.MessageConfirmDenyAccount))) return
        await this.$broker.denySignup(signup.id)
        this.signups = this.signups.filter((s) => s.id !== signup.id)
      })
    },
    approveRequest(request) {
      return this.run(`request-${request.id}`, async () => {
        await this.$broker.approveRequest(request.id)
        this.$toast.success(this.$getString('ToastDiscoverRequested', [request.title]))
        await this.load()
      })
    },
    denyRequest(request) {
      return this.run(`request-${request.id}`, async () => {
        if (!(await this.confirm(request.title, this.$getString('MessageConfirmDenyRequest', [request.username])))) return
        await this.$broker.denyRequest(request.id)
        await this.load()
      })
    },
    cancelRequest(book) {
      return this.run(book.key, async () => {
        if (!(await this.confirm(book.title, this.$strings.MessageConfirmCancelOwnRequest))) return
        await this.$broker.cancelRequest(book.id)
        await this.load()
      })
    },
    searchAgain(book) {
      return this.run(book.key, async () => {
        await this.$broker.searchAgain(book.id)
        this.$toast.success(this.$strings.ToastRequestSearchStarted)
        // The search runs in the background; a hit shows up as downloading on the next refresh
        setTimeout(this.load, 5000)
      })
    },
    cancelBook(book) {
      return this.run(book.key, async () => {
        if (!(await this.confirm(book.title, this.$strings.MessageConfirmCancelRequest))) return
        await this.$broker.cancelBook(book.id)
        this.books = this.books.filter((b) => b.key !== book.key)
      })
    },
    tryAnotherRelease(book) {
      return this.run(book.key, async () => {
        if (!(await this.confirm(book.title, this.$strings.MessageConfirmTryAnotherRelease))) return
        const outcome = await this.$broker.tryAnotherRelease(book.id)
        if (outcome === 'new') this.$toast.success(this.$strings.ToastAnotherReleaseFound)
        else if (outcome === 'same') this.$toast.warning(this.$strings.ToastOnlySameReleaseFound)
        else this.$toast.info(this.$strings.ToastNoOtherReleaseFound)
        await this.load()
      })
    }
  },
  mounted() {
    this.load()
    this.refreshInterval = setInterval(() => {
      if (!this.loading && !this.busyId && !this.showBookModal) this.load()
    }, REFRESH_INTERVAL_MS)
  },
  beforeDestroy() {
    clearInterval(this.refreshInterval)
  }
}
</script>
