<template>
  <modals-modal v-model="show" :width="400" height="100%">
    <div class="w-full h-full overflow-hidden absolute top-0 left-0 flex items-center justify-center" @click="show = false">
      <div v-if="book" class="w-full overflow-x-hidden overflow-y-auto bg-primary rounded-lg border border-border p-4" style="max-height: 80%" @click.stop>
        <div class="flex">
          <div class="flex-shrink-0 w-24 h-36 rounded-sm overflow-hidden bg-bg box-shadow-book">
            <img v-if="book.coverUrl && !imageFailed" :src="book.coverUrl" class="w-full h-full object-cover" @error="imageFailed = true" />
          </div>
          <div class="pl-4 min-w-0">
            <p class="text-lg font-semibold leading-tight">{{ book.title }}</p>
            <p v-if="book.author" class="text-sm text-fg-muted pt-1">{{ book.author }}</p>
            <p v-if="book.year" class="text-xs text-fg-muted pt-0.5">{{ book.year }}</p>
          </div>
        </div>

        <!-- Availability -->
        <div class="pt-4">
          <div v-if="checking" class="flex items-center text-sm text-fg-muted">
            <span class="material-symbols text-lg mr-2 animate-spin">progress_activity</span>
            <p>{{ $strings.MessageDiscoverChecking }}</p>
          </div>

          <template v-else>
            <div v-if="libraryItemId" class="flex items-center justify-between">
              <div class="flex items-center text-success text-sm">
                <span class="material-symbols fill text-lg mr-1">check_circle</span>
                <p>{{ $strings.LabelDiscoverInLibrary }}</p>
              </div>
              <ui-btn small color="primary" @click="openLibraryItem">{{ $strings.ButtonOpen }}</ui-btn>
            </div>

            <div v-else-if="statusLabel" class="flex items-center text-sm" :class="llStatus === 'library' ? 'text-success' : 'text-warning'">
              <span class="material-symbols text-lg mr-1">{{ statusIcon }}</span>
              <p>{{ statusLabel }}</p>
            </div>

            <template v-else-if="!llConfigured">
              <p class="text-sm text-fg-muted">{{ $strings.MessageDiscoverSetupLazyLibrarian }}</p>
              <ui-btn small color="primary" class="mt-2" @click="goToSettings">{{ $strings.HeaderSettings }}</ui-btn>
            </template>

            <template v-else>
              <ui-btn color="success" class="w-full" :loading="requesting" @click="requestAudiobook">
                <span class="material-symbols text-lg align-middle mr-1">add</span>
                <span class="align-middle">{{ $strings.ButtonRequestAudiobook }}</span>
              </ui-btn>
            </template>

            <p v-if="llError" class="text-xs text-error pt-2">{{ llError }}</p>
          </template>
        </div>

        <p v-if="description" class="text-sm text-fg-muted pt-4 whitespace-pre-line" :class="showFullDescription ? '' : 'line-clamp-6'" @click="showFullDescription = !showFullDescription">{{ description }}</p>
      </div>
    </div>
  </modals-modal>
</template>

<script>
export default {
  props: {
    value: Boolean,
    book: {
      type: Object,
      default: null
    }
  },
  data() {
    return {
      imageFailed: false,
      checking: false,
      requesting: false,
      llConfigured: false,
      llStatus: null,
      llError: null,
      libraryItemId: null,
      description: '',
      showFullDescription: false
    }
  },
  computed: {
    show: {
      get() {
        return this.value
      },
      set(val) {
        this.$emit('input', val)
      }
    },
    currentLibraryId() {
      return this.$store.state.libraries.currentLibraryId
    },
    statusLabel() {
      if (this.llStatus === 'library') return this.$strings.LabelDiscoverInLibrary
      if (this.llStatus === 'requested') return this.$strings.LabelDiscoverRequested
      if (this.llStatus === 'downloading') return this.$strings.LabelDiscoverDownloading
      return null
    },
    statusIcon() {
      if (this.llStatus === 'library') return 'check_circle'
      if (this.llStatus === 'downloading') return 'downloading'
      return 'schedule'
    }
  },
  watch: {
    value(newVal) {
      if (newVal) this.init()
    }
  },
  methods: {
    normalizeTitle(title) {
      return (title || '')
        .toLowerCase()
        .split(/[:(]/)[0]
        .replace(/^(the|a|an)\s+/, '')
        .replace(/[^a-z0-9]+/g, ' ')
        .trim()
    },
    /**
     * Audiobookshelf has no shared id with Open Library, so match on title plus the author's last name
     */
    async findInLibrary() {
      if (!this.currentLibraryId || !this.$store.state.user.serverConnectionConfig) return null
      const query = encodeURIComponent(this.book.title.split(':')[0])
      const results = await this.$nativeHttp.get(`/api/libraries/${this.currentLibraryId}/search?q=${query}&limit=10`).catch((error) => {
        console.error('[Discover] Library search failed', error)
        return null
      })
      const wantedTitle = this.normalizeTitle(this.book.title)
      const authorLastName = (this.book.author.split(',')[0].trim().split(/\s+/).pop() || '').toLowerCase()
      const match = (results?.book || []).find(({ libraryItem }) => {
        const metadata = libraryItem?.media?.metadata || {}
        const authorName = (metadata.authorName || (metadata.authors || []).map((a) => a.name).join(', ')).toLowerCase()
        return this.normalizeTitle(metadata.title) === wantedTitle && (!authorLastName || authorName.includes(authorLastName))
      })
      return match?.libraryItem?.id || null
    },
    async loadLazyLibrarianStatus() {
      await this.$lazylibrarian.getSettings()
      this.llConfigured = this.$lazylibrarian.isConfigured
      if (!this.llConfigured) return
      try {
        const statuses = await this.$lazylibrarian.getAudiobookStatuses()
        this.llStatus = statuses[this.book.id] || null
      } catch (error) {
        console.error('[Discover] LazyLibrarian status failed', error)
        this.llError = error.message
      }
    },
    async loadDescription() {
      try {
        const { description } = await this.$openlibrary.getWork(this.book.id)
        this.description = description
      } catch (error) {
        console.error('[Discover] Failed to load description', error)
      }
    },
    async init() {
      this.imageFailed = false
      this.llStatus = null
      this.llError = null
      this.libraryItemId = null
      this.description = ''
      this.showFullDescription = false
      this.checking = true

      const bookId = this.book.id
      this.loadDescription()
      const [libraryItemId] = await Promise.all([this.findInLibrary(), this.loadLazyLibrarianStatus()])
      // Ignore results if a different book was opened meanwhile
      if (this.book?.id !== bookId) return
      this.libraryItemId = libraryItemId
      this.checking = false
    },
    async requestAudiobook() {
      this.requesting = true
      this.llError = null
      try {
        await this.$lazylibrarian.requestAudiobook(this.book.id)
        this.llStatus = 'requested'
        this.$toast.success(this.$getString('ToastDiscoverRequested', [this.book.title]))
      } catch (error) {
        console.error('[Discover] Request failed', error)
        this.llError = error.message
      }
      this.requesting = false
    },
    openLibraryItem() {
      this.show = false
      this.$router.push(`/item/${this.libraryItemId}`)
    },
    goToSettings() {
      this.show = false
      this.$router.push('/settings')
    }
  }
}
</script>
