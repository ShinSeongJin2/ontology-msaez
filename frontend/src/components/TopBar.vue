<script setup>
import { ref } from 'vue'
import { useCanvasStore } from '../stores/canvas'
import IngestionModal from './IngestionModal.vue'

const canvasStore = useCanvasStore()
const showIngestionModal = ref(false)

function handleIngestionComplete() {
  // Modal will trigger navigator refresh
}
</script>

<template>
  <header class="top-bar">
    <div class="top-bar__logo">
      <div class="top-bar__logo-icon"></div>
      <span>Event Storming Navigator</span>
    </div>
    
    <div class="top-bar__divider"></div>
    
    <!-- Upload Button -->
    <button 
      class="upload-btn"
      @click="showIngestionModal = true"
      title="요구사항 문서 업로드"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="17 8 12 3 7 8"></polyline>
        <line x1="12" y1="3" x2="12" y2="15"></line>
      </svg>
      <span>문서 업로드</span>
    </button>
    
    <div class="top-bar__divider"></div>
    
    <div class="top-bar__info">
      <span>
        <strong>{{ canvasStore.nodes.length }}</strong> nodes on canvas
      </span>
      <span>•</span>
      <span>
        <strong>{{ canvasStore.edges.length }}</strong> connections
      </span>
    </div>
    
    <div style="flex: 1;"></div>
    
    <button 
      v-if="canvasStore.nodes.length > 0"
      class="canvas-toolbar__btn"
      @click="canvasStore.clearCanvas()"
      title="Clear Canvas"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="3 6 5 6 21 6"></polyline>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
      </svg>
    </button>
    
    <!-- Ingestion Modal -->
    <IngestionModal 
      v-model="showIngestionModal"
      @complete="handleIngestionComplete"
    />
  </header>
</template>

<style scoped>
.upload-btn {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  background: linear-gradient(135deg, var(--color-accent) 0%, #1c7ed6 100%);
  border: none;
  border-radius: var(--radius-md);
  color: white;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}

.upload-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(34, 139, 230, 0.4);
}

.upload-btn:active {
  transform: translateY(0);
}
</style>

