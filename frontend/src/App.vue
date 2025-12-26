<script setup>
import { ref, provide } from 'vue'
import TopBar from './components/TopBar.vue'
import LeftPanel from './components/LeftPanel.vue'
import RightPanel from './components/RightPanel.vue'
import UserStoryEditModal from './components/UserStoryEditModal.vue'
import { useCanvasStore } from './stores/canvas'
import { useNavigatorStore } from './stores/navigator'

const canvasStore = useCanvasStore()
const navigatorStore = useNavigatorStore()

// User Story Edit Modal state
const showEditModal = ref(false)
const editingUserStory = ref(null)

function handleEditUserStory(userStory) {
  editingUserStory.value = userStory
  showEditModal.value = true
}

function handleCloseEditModal() {
  showEditModal.value = false
  editingUserStory.value = null
}

async function handleUserStorySaved() {
  // Refresh the navigator to reflect changes
  await navigatorStore.refreshAll()
}

// Provide canvas store and edit function to child components
provide('canvasStore', canvasStore)
provide('editUserStory', handleEditUserStory)
</script>

<template>
  <div class="app-container">
    <TopBar />
    <div class="main-content">
      <LeftPanel />
      <RightPanel />
    </div>
    
    <!-- User Story Edit Modal -->
    <UserStoryEditModal 
      :visible="showEditModal"
      :user-story="editingUserStory"
      @close="handleCloseEditModal"
      @saved="handleUserStorySaved"
    />
  </div>
</template>

