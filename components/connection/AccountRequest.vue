<template>
  <div v-if="available" class="pt-2">
    <div class="w-full h-px bg-fg/10 my-4" />

    <!-- No request yet -->
    <template v-if="step === 'idle'">
      <p class="text-sm text-fg-muted pb-2">{{ $strings.MessageNoAccountYet }}</p>
      <ui-btn class="w-full h-10" type="button" @click="step = 'form'">{{ $strings.ButtonRequestAccount }}</ui-btn>
    </template>

    <!-- Request form -->
    <form v-else-if="step === 'form'" @submit.prevent="submitRequest">
      <p class="text-base pb-2">{{ $strings.ButtonRequestAccount }}</p>
      <ui-text-input v-model="username" :disabled="processing" :placeholder="$strings.LabelUsername" class="w-full mb-2 text-lg" />
      <ui-text-input v-model="displayName" :disabled="processing" :placeholder="$strings.LabelYourName" class="w-full mb-2 text-lg" />
      <ui-text-input v-model="note" :disabled="processing" :placeholder="$strings.LabelNoteForAdmin" class="w-full mb-2 text-lg" />
      <p class="text-xs text-fg-muted pb-2">{{ $strings.MessageAccountRequestHelp }}</p>
      <div class="flex items-center">
        <ui-btn type="button" :disabled="processing" class="h-10" @click="step = 'idle'">{{ $strings.ButtonCancel }}</ui-btn>
        <div class="flex-grow" />
        <ui-btn type="submit" :loading="processing" :disabled="!username.trim()" class="h-10">{{ $strings.ButtonSendRequest }}</ui-btn>
      </div>
    </form>

    <!-- Waiting for the admin -->
    <template v-else-if="step === 'pending'">
      <div class="flex items-start">
        <span class="material-symbols text-warning text-xl mr-2">hourglass_top</span>
        <p class="text-sm">{{ $getString('MessageAccountRequestPending', [stored.username]) }}</p>
      </div>
      <div class="flex items-center pt-3">
        <ui-btn type="button" small :disabled="processing" @click="forgetRequest">{{ $strings.ButtonCancel }}</ui-btn>
        <div class="flex-grow" />
        <ui-btn type="button" small :loading="processing" @click="checkStatus(true)">{{ $strings.ButtonCheckAgain }}</ui-btn>
      </div>
    </template>

    <!-- Approved: choose a password -->
    <form v-else-if="step === 'approved'" @submit.prevent="completeRequest">
      <div class="flex items-start pb-3">
        <span class="material-symbols fill text-success text-xl mr-2">check_circle</span>
        <p class="text-sm">{{ $getString('MessageAccountApprovedChoosePassword', [stored.username]) }}</p>
      </div>
      <ui-text-input v-model="password" type="password" :disabled="processing" :placeholder="$strings.LabelPassword" class="w-full mb-2 text-lg" />
      <ui-text-input v-model="passwordConfirm" type="password" :disabled="processing" :placeholder="$strings.LabelConfirmPassword" class="w-full mb-2 text-lg" />
      <div class="flex justify-end">
        <ui-btn type="submit" :loading="processing" :disabled="!password" class="h-10">{{ $strings.ButtonCreateAccount }}</ui-btn>
      </div>
    </form>

    <!-- Denied or expired -->
    <template v-else-if="step === 'closed'">
      <p class="text-sm text-fg-muted">{{ closedMessage }}</p>
      <ui-btn type="button" small class="mt-3" @click="forgetRequest">{{ $strings.ButtonOk }}</ui-btn>
    </template>

    <p v-if="error" class="text-sm text-error pt-2">{{ error }}</p>
  </div>
</template>

<script>
import { Preferences } from '@capacitor/preferences'

const STORAGE_KEY = 'frogshelfAccountRequest'

export default {
  props: {
    serverAddress: String
  },
  data() {
    return {
      available: false,
      step: 'idle',
      processing: false,
      error: null,
      username: '',
      displayName: '',
      note: '',
      password: '',
      passwordConfirm: '',
      stored: null,
      closedStatus: null
    }
  },
  computed: {
    closedMessage() {
      return this.closedStatus === 'denied' ? this.$strings.MessageAccountRequestDenied : this.$strings.MessageAccountRequestExpired
    }
  },
  watch: {
    serverAddress() {
      this.init()
    }
  },
  methods: {
    async loadStored() {
      try {
        const { value } = (await Preferences.get({ key: STORAGE_KEY })) || {}
        const stored = value ? JSON.parse(value) : null
        return stored?.serverAddress === this.serverAddress ? stored : null
      } catch {
        return null
      }
    },
    async saveStored(stored) {
      this.stored = stored
      if (stored) await Preferences.set({ key: STORAGE_KEY, value: JSON.stringify(stored) })
      else await Preferences.remove({ key: STORAGE_KEY })
    },
    async submitRequest() {
      this.processing = true
      this.error = null
      try {
        const username = this.username.trim()
        const result = await this.$broker.requestAccount(this.serverAddress, { username, displayName: this.displayName.trim(), note: this.note.trim() })
        await this.saveStored({ serverAddress: this.serverAddress, id: result.id, token: result.token, username })
        this.step = 'pending'
      } catch (error) {
        this.error = error.message
      }
      this.processing = false
    },
    async checkStatus(userInitiated = false) {
      if (!this.stored) return
      this.processing = userInitiated
      this.error = null
      try {
        const { status } = await this.$broker.getAccountRequest(this.serverAddress, this.stored.id, this.stored.token)
        if (status === 'pending') this.step = 'pending'
        else if (status === 'approved') this.step = 'approved'
        else if (status === 'completed') await this.forgetRequest()
        else {
          this.closedStatus = status
          this.step = 'closed'
        }
      } catch (error) {
        if (error.status === 404) {
          this.closedStatus = 'expired'
          this.step = 'closed'
        } else {
          this.error = error.message
        }
      }
      this.processing = false
    },
    async completeRequest() {
      this.error = null
      if (this.password.length < 8) {
        this.error = this.$strings.MessagePasswordTooShort
        return
      }
      if (this.password !== this.passwordConfirm) {
        this.error = this.$strings.MessagePasswordsDontMatch
        return
      }
      this.processing = true
      try {
        const { username } = await this.$broker.completeAccountRequest(this.serverAddress, this.stored.id, this.stored.token, this.password)
        const password = this.password
        await this.saveStored(null)
        this.password = ''
        this.passwordConfirm = ''
        this.step = 'idle'
        // The connect form signs in with these
        this.$emit('account-ready', { username, password })
      } catch (error) {
        this.error = error.message
      }
      this.processing = false
    },
    async forgetRequest() {
      await this.saveStored(null)
      this.closedStatus = null
      this.error = null
      this.step = 'idle'
    },
    async init() {
      this.available = false
      this.step = 'idle'
      this.error = null
      if (!this.serverAddress) return
      const info = await this.$broker.getInfo(this.serverAddress)
      this.available = !!info?.signup
      if (!this.available) return
      this.stored = await this.loadStored()
      if (this.stored) await this.checkStatus()
    }
  },
  mounted() {
    this.init()
  }
}
</script>
