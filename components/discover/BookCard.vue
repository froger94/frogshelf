<template>
  <div class="flex-shrink-0 cursor-pointer" :style="{ width: width + 'px' }" @click="$emit('click', book)">
    <div class="relative w-full rounded-sm overflow-hidden bg-primary box-shadow-book" :style="{ height: coverHeight + 'px' }">
      <img v-if="book.coverUrl && !imageFailed" :src="book.coverUrl" loading="lazy" class="w-full h-full object-cover" @error="imageFailed = true" />
      <div v-else class="w-full h-full flex flex-col items-center justify-center p-2 text-center">
        <p class="text-xs font-semibold line-clamp-3">{{ book.title }}</p>
        <p v-if="book.author" class="text-xxs text-fg-muted pt-1 line-clamp-2">{{ book.author }}</p>
      </div>
    </div>
    <p class="text-xs font-semibold pt-1.5 truncate">{{ book.title }}</p>
    <p class="text-xxs text-fg-muted truncate">{{ book.author || '&nbsp;' }}</p>
  </div>
</template>

<script>
export default {
  props: {
    book: {
      type: Object,
      required: true
    },
    width: {
      type: Number,
      default: 100
    }
  },
  data() {
    return {
      imageFailed: false
    }
  },
  computed: {
    coverHeight() {
      return this.width * 1.5
    }
  }
}
</script>
