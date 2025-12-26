<script setup>
import { ref } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import { useCanvasStore } from '../stores/canvas'

// Custom Nodes
import CommandNode from './nodes/CommandNode.vue'
import EventNode from './nodes/EventNode.vue'
import PolicyNode from './nodes/PolicyNode.vue'
import AggregateNode from './nodes/AggregateNode.vue'
import BoundedContextNode from './nodes/BoundedContextNode.vue'

const canvasStore = useCanvasStore()
const isDragOver = ref(false)

const { fitView, zoomIn, zoomOut } = useVueFlow()

// Node types mapping
const nodeTypes = {
  command: CommandNode,
  event: EventNode,
  policy: PolicyNode,
  aggregate: AggregateNode,
  boundedcontext: BoundedContextNode
}

// MiniMap node color
function getNodeColor(node) {
  const colors = {
    command: '#5c7cfa',
    event: '#fd7e14',
    policy: '#b197fc',
    aggregate: '#fcc419',
    boundedcontext: '#373a40'
  }
  return colors[node.type] || '#909296'
}

// Handle drop from navigator
async function handleDrop(event) {
  event.preventDefault()
  isDragOver.value = false
  
  const data = event.dataTransfer.getData('application/json')
  if (!data) return
  
  try {
    const { nodeId } = JSON.parse(data)
    
    // Use the new API that includes BC context
    const response = await fetch(`/api/graph/expand-with-bc/${nodeId}`)
    const expandedData = await response.json()
    
    // Track new node IDs for cross-BC relation finding
    const newNodeIds = expandedData.nodes.map(n => n.id)
    
    canvasStore.addNodesWithLayout(
      expandedData.nodes, 
      expandedData.relationships, 
      expandedData.bcContext
    )
    
    // Find cross-BC relations (Event → TRIGGERS → Policy between BCs)
    await canvasStore.findCrossBCRelations(newNodeIds)
    
    // Also find any other relations
    await canvasStore.findAndAddRelations()
    
    // Fit view after adding nodes
    setTimeout(() => fitView({ padding: 0.3 }), 150)
  } catch (error) {
    console.error('Failed to handle drop:', error)
  }
}

function handleDragOver(event) {
  event.preventDefault()
  isDragOver.value = true
  event.dataTransfer.dropEffect = 'copy'
}

function handleDragLeave() {
  isDragOver.value = false
}

// Watch for node changes and update positions
function onNodesChange(changes) {
  changes.forEach(change => {
    if (change.type === 'position' && change.position) {
      canvasStore.updateNodePosition(change.id, change.position)
    }
  })
}
</script>

<template>
  <div 
    class="right-panel"
    :class="{ 'drop-zone-active': isDragOver }"
    @drop="handleDrop"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
  >
    <div class="canvas-container">
      <!-- Empty State -->
      <div v-if="canvasStore.nodes.length === 0" class="canvas-empty">
        <div class="canvas-empty__icon">📋</div>
        <div class="canvas-empty__text">Canvas is empty</div>
        <div class="canvas-empty__hint">
          Drag items from the navigator or double-click to add
        </div>
      </div>
      
      <!-- Vue Flow Canvas -->
      <VueFlow
        v-else
        :nodes="canvasStore.nodes"
        :edges="canvasStore.edges"
        :node-types="nodeTypes"
        :default-viewport="{ zoom: 0.8, x: 50, y: 50 }"
        :min-zoom="0.2"
        :max-zoom="2"
        :snap-to-grid="true"
        :snap-grid="[10, 10]"
        :nodes-draggable="true"
        :nodes-connectable="false"
        :pan-on-drag="true"
        :zoom-on-scroll="true"
        :prevent-scrolling="true"
        fit-view-on-init
        @nodes-change="onNodesChange"
      >
        <Background pattern-color="#2a2a3a" :gap="20" />
        <Controls position="bottom-left" />
        <MiniMap 
          :node-color="getNodeColor"
          :node-stroke-width="3"
          pannable
          zoomable
        />
      </VueFlow>
    </div>
    
    <!-- Canvas Toolbar -->
    <div v-if="canvasStore.nodes.length > 0" class="canvas-toolbar">
      <button 
        class="canvas-toolbar__btn"
        @click="zoomIn()"
        title="Zoom In"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          <line x1="11" y1="8" x2="11" y2="14"></line>
          <line x1="8" y1="11" x2="14" y2="11"></line>
        </svg>
      </button>
      
      <button 
        class="canvas-toolbar__btn"
        @click="zoomOut()"
        title="Zoom Out"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          <line x1="8" y1="11" x2="14" y2="11"></line>
        </svg>
      </button>
      
      <button 
        class="canvas-toolbar__btn"
        @click="fitView({ padding: 0.3 })"
        title="Fit View"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"></path>
        </svg>
      </button>
      
      <div class="canvas-toolbar__divider"></div>
      
      <button 
        class="canvas-toolbar__btn"
        @click="canvasStore.findAndAddRelations()"
        title="Find Relations"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="18" cy="5" r="3"></circle>
          <circle cx="6" cy="12" r="3"></circle>
          <circle cx="18" cy="19" r="3"></circle>
          <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
          <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
        </svg>
      </button>
      
      <button 
        class="canvas-toolbar__btn"
        @click="canvasStore.clearCanvas()"
        title="Clear Canvas"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="3 6 5 6 21 6"></polyline>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
        </svg>
      </button>
    </div>
  </div>
</template>

<style>
@import '@vue-flow/core/dist/style.css';
@import '@vue-flow/core/dist/theme-default.css';
@import '@vue-flow/controls/dist/style.css';
@import '@vue-flow/minimap/dist/style.css';

/* Vue Flow custom background */
.vue-flow {
  background: #1a1b26 !important;
}

/* BC Group Node styling */
.vue-flow__node-boundedcontext {
  padding: 0 !important;
  border-radius: 16px !important;
  background: transparent !important;
  border: none !important;
}

/* Ensure child nodes are visible above parent */
.vue-flow__node {
  z-index: 1;
}

.vue-flow__node-boundedcontext {
  z-index: 0 !important;
}

/* Vue Flow Minimap custom styles */
.vue-flow__minimap {
  background: var(--color-bg-secondary) !important;
  border: 1px solid var(--color-border) !important;
  border-radius: var(--radius-md) !important;
}

/* Vue Flow Controls custom styles */
.vue-flow__controls {
  background: var(--color-bg-secondary) !important;
  border: 1px solid var(--color-border) !important;
  border-radius: var(--radius-md) !important;
  box-shadow: var(--shadow-md) !important;
}

.vue-flow__controls-button {
  background: transparent !important;
  border: none !important;
  color: var(--color-text) !important;
}

.vue-flow__controls-button:hover {
  background: var(--color-bg-tertiary) !important;
}

.vue-flow__controls-button svg {
  fill: var(--color-text) !important;
}

/* Edge labels */
.vue-flow__edge-textbg {
  fill: #1a1b26 !important;
}

.vue-flow__edge-text {
  fill: #c1c2c5 !important;
}
</style>
