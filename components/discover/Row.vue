<template>
  <div class="w-full py-3">
    <p class="px-4 pb-2 font-semibold">{{ label }}</p>
    <div class="flex px-4 max-w-full overflow-x-auto overflow-y-hidden gap-3 no-scrollbar">
      <template v-if="loading">
        <div v-for="n in 5" :key="n" class="flex-shrink-0" :style="{ width: cardWidth + 'px' }">
          <div class="w-full rounded-sm bg-primary animate-pulse" :style="{ height: cardWidth * 1.5 + 'px' }" />
          <div class="h-3 w-4/5 mt-2 rounded-sm bg-primary animate-pulse" />
        </div>
      </template>
      <p v-else-if="error" class="text-sm text-fg-muted py-6">{{ error }}</p>
      <template v-else>
        <discover-book-card v-for="book in books" :key="book.id" :book="book" :width="cardWidth" @click="$emit('select', book)" />
      </template>
    </div>
  </div>
</template>

<script>
export default {
  props: {
    label: String,
    books: {
      type: Array,
      default: () => []
    },
    loading: Boolean,
    error: String,
    cardWidth: {
      type: Number,
      default: 100
    }
  }
}
</script>

<style scoped>
.no-scrollbar {
  scrollbar-width: none;
}
.no-scrollbar::-webkit-scrollbar {
  display: none;
}
</style>
