<script setup>
import TopBar from '@/app/layout/TopBar.vue'
import NavigatorPanel from '@/features/navigator/ui/NavigatorPanel.vue'
import CanvasWorkspace from '@/features/canvas/ui/CanvasWorkspace.vue'
import UserStoryEditModal from '@/features/userStories/ui/UserStoryEditModal.vue'
import { useNavigatorStore } from '@/features/navigator/navigator.store'
import { useUserStoryEditorStore } from '@/features/userStories/userStoryEditor.store'

const navigatorStore = useNavigatorStore()
const userStoryEditor = useUserStoryEditorStore()

async function handleUserStorySaved() {
  // Refresh the navigator to reflect changes
  await navigatorStore.refreshAll()
}
</script>

<template>
  <div class="app-container">
    <TopBar />
    <div class="main-content">
      <NavigatorPanel />
      <CanvasWorkspace />
    </div>
    
    <!-- User Story Edit Modal -->
    <UserStoryEditModal 
      :visible="userStoryEditor.isOpen"
      :user-story="userStoryEditor.userStory"
      @close="userStoryEditor.close()"
      @saved="handleUserStorySaved"
    />
  </div>
</template>

