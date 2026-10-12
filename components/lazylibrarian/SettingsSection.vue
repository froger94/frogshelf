<template>
  <div @focusin="inputFocused" @focusout="inputBlurred">
    <p class="uppercase text-xs font-semibold text-fg-muted mb-2 mt-10">{{ $strings.HeaderLazyLibrarianSettings }}</p>
    <div class="py-2" data-ll-field>
      <ui-text-input-with-label v-model="address" :label="$strings.LabelLazyLibrarianAddress" placeholder="http://192.168.1.10:5299" :autofocus="false" type="url" />
    </div>
    <div class="py-2" data-ll-field>
      <ui-text-input-with-label v-model="apiKey" :label="$strings.LabelLazyLibrarianApiKey" :autofocus="false" type="password" />
      <p class="text-xs text-fg-muted pt-1">{{ $strings.MessageLazyLibrarianApiKeyHelp }}</p>
    </div>

    <div class="flex items-center py-3">
      <ui-btn small :loading="testing" :disabled="!canSubmit || saving" color="primary" class="mr-2" @click="testConnection">{{ $strings.ButtonTestConnection }}</ui-btn>
      <ui-btn small :loading="saving" :disabled="!isDirty || testing" color="success" @click="save">{{ $strings.ButtonSave }}</ui-btn>
    </div>

    <div v-if="status" class="flex items-start py-1 text-sm" :class="status.success ? 'text-success' : 'text-error'">
      <span class="material-symbols text-lg mr-1">{{ status.success ? 'check_circle' : 'error' }}</span>
      <p class="pt-0.5 break-words min-w-0">{{ status.message }}</p>
    </div>

    <!-- This section is last on the Settings page, so without extra room below it the page
         can't scroll a focused field above the on-screen keyboard -->
    <div v-if="keyboardSpacer" style="height: 60vh" />
  </div>
</template>

<script>
export default {
  data() {
    return {
      address: '',
      apiKey: '',
      savedSettings: { address: '', apiKey: '' },
      testing: false,
      saving: false,
      status: null,
      keyboardSpacer: false,
      blurTimeout: null
    }
  },
  computed: {
    canSubmit() {
      return !!(this.address.trim() && this.apiKey.trim())
    },
    isDirty() {
      return this.$lazylibrarian.normalizeAddress(this.address) !== this.savedSettings.address || this.apiKey.trim() !== this.savedSettings.apiKey
    }
  },
  watch: {
    address() {
      this.status = null
    },
    apiKey() {
      this.status = null
    }
  },
  methods: {
    inputFocused(event) {
      if (event.target?.tagName !== 'INPUT') return
      clearTimeout(this.blurTimeout)
      this.keyboardSpacer = true
      // The keyboard overlays the WebView instead of resizing it, so "center" would sit right at the
      // keyboard's edge. Put the field (with its label, help text and the buttons below) at the top instead.
      const field = event.target.closest('[data-ll-field]') || event.target
      setTimeout(() => field.scrollIntoView({ block: 'start', behavior: 'smooth' }), 300)
    },
    inputBlurred() {
      // Delay so moving between the two fields doesn't make the page jump
      this.blurTimeout = setTimeout(() => (this.keyboardSpacer = false), 200)
    },
    async testConnection() {
      this.testing = true
      this.status = null
      try {
        const { version, installType } = await this.$lazylibrarian.testConnection({ address: this.address, apiKey: this.apiKey })
        const details = [version, installType].filter(Boolean).join(', ')
        this.status = { success: true, message: this.$getString('MessageLazyLibrarianConnected', [details]) }
      } catch (error) {
        this.status = { success: false, message: error.message }
      }
      this.testing = false
    },
    async save() {
      this.saving = true
      this.savedSettings = await this.$lazylibrarian.saveSettings({ address: this.address, apiKey: this.apiKey })
      this.address = this.savedSettings.address
      this.apiKey = this.savedSettings.apiKey
      this.saving = false
      this.$toast.success(this.$strings.ToastLazyLibrarianSettingsSaved)
    }
  },
  beforeDestroy() {
    clearTimeout(this.blurTimeout)
  },
  async mounted() {
    this.savedSettings = await this.$lazylibrarian.getSettings()
    this.address = this.savedSettings.address
    this.apiKey = this.savedSettings.apiKey
  }
}
</script>
