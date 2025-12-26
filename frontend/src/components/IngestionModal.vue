<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useNavigatorStore } from '../stores/navigator'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'complete'])

const navigatorStore = useNavigatorStore()

// State
const dragActive = ref(false)
const file = ref(null)
const textContent = ref('')
const inputMode = ref('file') // 'file' or 'text'
const isUploading = ref(false)
const isProcessing = ref(false)
const sessionId = ref(null)
const progress = ref(0)
const currentPhase = ref('')
const currentMessage = ref('')
const createdItems = ref([])
const eventSource = ref(null)
const error = ref(null)
const summary = ref(null)
const isPanelMinimized = ref(false)

// Data clearing state
const showClearConfirm = ref(false)
const existingDataStats = ref(null)
const isLoadingStats = ref(false)
const isClearing = ref(false)

// Computed
const isOpen = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const canSubmit = computed(() => {
  if (inputMode.value === 'file') {
    return file.value !== null
  }
  return textContent.value.trim().length > 10
})

const phaseLabel = computed(() => {
  const labels = {
    'upload': '업로드 중',
    'parsing': '문서 파싱',
    'extracting_user_stories': 'User Story 추출',
    'identifying_bc': 'BC 식별',
    'extracting_aggregates': 'Aggregate 추출',
    'extracting_commands': 'Command 추출',
    'extracting_events': 'Event 추출',
    'identifying_policies': 'Policy 식별',
    'saving': '저장 중',
    'complete': '완료',
    'error': '오류'
  }
  return labels[currentPhase.value] || currentPhase.value
})

// Show floating panel when processing or has summary
const showFloatingPanel = computed(() => {
  return isProcessing.value || summary.value !== null
})

// Has existing data
const hasExistingData = computed(() => {
  return existingDataStats.value && existingDataStats.value.total > 0
})

// Watch for modal open to check existing data
watch(isOpen, async (newVal) => {
  if (newVal) {
    await checkExistingData()
  }
})

// Methods
function handleDragOver(e) {
  e.preventDefault()
  dragActive.value = true
}

function handleDragLeave(e) {
  e.preventDefault()
  dragActive.value = false
}

function handleDrop(e) {
  e.preventDefault()
  dragActive.value = false
  
  const files = e.dataTransfer.files
  if (files.length > 0) {
    handleFile(files[0])
  }
}

function handleFileSelect(e) {
  const files = e.target.files
  if (files.length > 0) {
    handleFile(files[0])
  }
}

function handleFile(f) {
  const validTypes = ['text/plain', 'application/pdf', 'text/markdown']
  const validExtensions = ['.txt', '.pdf', '.md']
  
  const isValidType = validTypes.includes(f.type) || 
    validExtensions.some(ext => f.name.toLowerCase().endsWith(ext))
  
  if (!isValidType) {
    error.value = '지원하지 않는 파일 형식입니다. (txt, pdf, md 지원)'
    return
  }
  
  file.value = f
  error.value = null
}

function removeFile() {
  file.value = null
}

// Check for existing data in Neo4j
async function checkExistingData() {
  isLoadingStats.value = true
  try {
    const response = await fetch('/api/graph/stats')
    if (response.ok) {
      existingDataStats.value = await response.json()
    }
  } catch (e) {
    console.error('Failed to fetch stats:', e)
    existingDataStats.value = null
  } finally {
    isLoadingStats.value = false
  }
}

// Clear all existing data from Neo4j
async function clearExistingData() {
  isClearing.value = true
  try {
    const response = await fetch('/api/graph/clear', { method: 'DELETE' })
    if (response.ok) {
      existingDataStats.value = { total: 0, by_type: {} }
      navigatorStore.clearAll()
      return true
    }
    return false
  } catch (e) {
    error.value = '데이터 삭제 중 오류가 발생했습니다.'
    return false
  } finally {
    isClearing.value = false
  }
}

// Handle start button click
function handleStartClick() {
  if (hasExistingData.value) {
    showClearConfirm.value = true
  } else {
    startIngestion()
  }
}

// User chose to clear existing data and proceed
async function confirmClearAndStart() {
  showClearConfirm.value = false
  const cleared = await clearExistingData()
  if (cleared) {
    await startIngestion()
  }
}

// User chose to cancel
function cancelClear() {
  showClearConfirm.value = false
}

async function startIngestion() {
  error.value = null
  isUploading.value = true
  createdItems.value = []
  summary.value = null
  isPanelMinimized.value = false
  
  try {
    const formData = new FormData()
    
    if (inputMode.value === 'file' && file.value) {
      formData.append('file', file.value)
    } else {
      formData.append('text', textContent.value)
    }
    
    const uploadResponse = await fetch('/api/ingest/upload', {
      method: 'POST',
      body: formData
    })
    
    if (!uploadResponse.ok) {
      const errData = await uploadResponse.json()
      throw new Error(errData.detail || 'Upload failed')
    }
    
    const { session_id } = await uploadResponse.json()
    sessionId.value = session_id
    isUploading.value = false
    isProcessing.value = true
    
    // Close the upload modal, show floating panel
    isOpen.value = false
    
    // Connect to SSE stream
    connectToStream(session_id)
    
  } catch (e) {
    error.value = e.message
    isUploading.value = false
    isProcessing.value = false
  }
}

function connectToStream(sid) {
  eventSource.value = new EventSource(`/api/ingest/stream/${sid}`)
  
  eventSource.value.addEventListener('progress', (e) => {
    const data = JSON.parse(e.data)
    
    currentPhase.value = data.phase
    currentMessage.value = data.message
    progress.value = data.progress
    
    // Handle created objects
    if (data.data?.object) {
      const obj = data.data.object
      createdItems.value.push(obj)
      
      // Trigger navigator updates for dynamic display
      if (obj.type === 'UserStory') {
        navigatorStore.addUserStory(obj)
      } else if (obj.type === 'BoundedContext') {
        navigatorStore.addContext(obj)
      } else if (obj.type === 'Aggregate') {
        navigatorStore.addAggregate(obj)
      } else if (obj.type === 'Command') {
        navigatorStore.addCommand(obj)
      } else if (obj.type === 'Event') {
        navigatorStore.addEvent(obj)
      } else if (obj.type === 'Policy') {
        navigatorStore.addPolicy(obj)
      }
    }
    
    // Handle User Story assignment to BC (move animation)
    if (data.data?.type === 'UserStoryAssigned') {
      const assignment = data.data.object
      navigatorStore.assignUserStoryToBC(
        assignment.id,
        assignment.targetBcId,
        assignment.targetBcName
      )
    }
    
    // Handle summary
    if (data.data?.summary) {
      summary.value = data.data.summary
    }
    
    // Handle completion
    if (data.phase === 'complete') {
      isProcessing.value = false
      closeStream()
      navigatorStore.refreshAll()
    }
    
    // Handle error
    if (data.phase === 'error') {
      error.value = data.message
      isProcessing.value = false
      closeStream()
    }
  })
  
  eventSource.value.onerror = () => {
    if (isProcessing.value) {
      error.value = '연결이 끊어졌습니다'
    }
    closeStream()
  }
}

function closeStream() {
  if (eventSource.value) {
    eventSource.value.close()
    eventSource.value = null
  }
}

function closeModal() {
  // Reset state
  file.value = null
  textContent.value = ''
  error.value = null
  showClearConfirm.value = false
  isOpen.value = false
}

function closeFloatingPanel() {
  if (isProcessing.value) {
    if (!confirm('진행 중인 작업이 있습니다. 정말 닫으시겠습니까?')) {
      return
    }
    closeStream()
    isProcessing.value = false
  }
  
  // Reset state
  progress.value = 0
  currentPhase.value = ''
  currentMessage.value = ''
  createdItems.value = []
  summary.value = null
  
  emit('complete')
}

function toggleMinimize() {
  isPanelMinimized.value = !isPanelMinimized.value
}

function getTypeIcon(type) {
  const icons = {
    UserStory: 'US',
    BoundedContext: 'BC',
    Aggregate: 'A',
    Command: 'C',
    Event: 'E',
    Policy: 'P'
  }
  return icons[type] || '?'
}

function getTypeClass(type) {
  return `item-icon--${type.toLowerCase()}`
}

// Cleanup on unmount
onUnmounted(() => {
  closeStream()
})

// Sample requirements text
const sampleText = `# 온라인 쇼핑몰 요구사항

## 1. 주문 관리
- 고객은 상품을 장바구니에 담고 주문할 수 있어야 한다
- 고객은 주문을 취소할 수 있어야 한다 (배송 전까지)
- 고객은 주문 상태를 조회할 수 있어야 한다

## 2. 상품 관리
- 판매자는 상품을 등록할 수 있어야 한다
- 판매자는 상품 정보를 수정할 수 있어야 한다
- 판매자는 상품 재고를 관리할 수 있어야 한다

## 3. 결제 처리
- 시스템은 주문 시 결제를 처리해야 한다
- 주문 취소 시 자동으로 환불이 처리되어야 한다

## 4. 재고 관리
- 주문 시 재고가 자동으로 차감되어야 한다
- 주문 취소 시 재고가 복원되어야 한다

## 5. 알림
- 주문 완료 시 고객에게 이메일 알림을 보내야 한다
- 배송 시작 시 고객에게 알림을 보내야 한다`

function useSample() {
  textContent.value = sampleText
  inputMode.value = 'text'
}
</script>

<template>
  <!-- Upload Dialog (initial file selection) - Blocking Modal -->
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="isOpen && !isProcessing" class="modal-overlay" @click.self="closeModal">
        <div class="modal-container">
          <!-- Header -->
          <div class="modal-header">
            <h2 class="modal-title">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="12" y1="18" x2="12" y2="12"></line>
                <line x1="9" y1="15" x2="15" y2="15"></line>
              </svg>
              요구사항 문서 업로드
            </h2>
            <button class="modal-close" @click="closeModal">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
          
          <!-- Body -->
          <div class="modal-body">
            <!-- Existing Data Warning -->
            <div v-if="hasExistingData && !showClearConfirm" class="existing-data-warning">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
              <span>
                기존 데이터가 있습니다: 
                <strong>{{ existingDataStats.total }}개</strong> 노드
              </span>
            </div>
            
            <!-- Clear Confirmation Dialog -->
            <div v-if="showClearConfirm" class="clear-confirm-dialog">
              <div class="clear-confirm-icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                  <line x1="12" y1="9" x2="12" y2="13"></line>
                  <line x1="12" y1="17" x2="12.01" y2="17"></line>
                </svg>
              </div>
              <h3 class="clear-confirm-title">기존 데이터 삭제 확인</h3>
              <p class="clear-confirm-message">
                새로운 요구사항을 분석하기 전에 기존 데이터를 모두 삭제해야 합니다.
              </p>
              <div class="clear-confirm-stats">
                <div v-for="(count, type) in existingDataStats.by_type" :key="type" class="stat-chip">
                  <span class="stat-chip-label">{{ type }}</span>
                  <span class="stat-chip-value">{{ count }}</span>
                </div>
              </div>
              <p class="clear-confirm-warning">
                ⚠️ 이 작업은 되돌릴 수 없습니다.
              </p>
              <div class="clear-confirm-actions">
                <button class="btn btn--secondary" @click="cancelClear" :disabled="isClearing">
                  취소
                </button>
                <button class="btn btn--danger" @click="confirmClearAndStart" :disabled="isClearing">
                  <template v-if="isClearing">
                    <span class="spinner"></span>
                    삭제 중...
                  </template>
                  <template v-else>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <polyline points="3 6 5 6 21 6"></polyline>
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                    </svg>
                    삭제하고 계속
                  </template>
                </button>
              </div>
            </div>
            
            <!-- Normal upload UI (hidden during confirm) -->
            <template v-if="!showClearConfirm">
              <!-- Input Mode Tabs -->
              <div class="input-tabs">
                <button 
                  :class="['tab-btn', { active: inputMode === 'file' }]"
                  @click="inputMode = 'file'"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path>
                    <polyline points="13 2 13 9 20 9"></polyline>
                  </svg>
                  파일 업로드
                </button>
                <button 
                  :class="['tab-btn', { active: inputMode === 'text' }]"
                  @click="inputMode = 'text'"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="17" y1="10" x2="3" y2="10"></line>
                    <line x1="21" y1="6" x2="3" y2="6"></line>
                    <line x1="21" y1="14" x2="3" y2="14"></line>
                    <line x1="17" y1="18" x2="3" y2="18"></line>
                  </svg>
                  텍스트 입력
                </button>
              </div>
              
              <!-- File Upload Area -->
              <div v-if="inputMode === 'file'" class="upload-section">
                <div 
                  class="dropzone"
                  :class="{ 'is-active': dragActive, 'has-file': file }"
                  @dragover="handleDragOver"
                  @dragleave="handleDragLeave"
                  @drop="handleDrop"
                  @click="$refs.fileInput.click()"
                >
                  <input 
                    ref="fileInput"
                    type="file" 
                    accept=".txt,.pdf,.md"
                    style="display: none"
                    @change="handleFileSelect"
                  />
                  
                  <div v-if="!file" class="dropzone-content">
                    <div class="dropzone-icon">
                      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="17 8 12 3 7 8"></polyline>
                        <line x1="12" y1="3" x2="12" y2="15"></line>
                      </svg>
                    </div>
                    <p class="dropzone-text">파일을 드래그하거나 클릭하여 선택</p>
                    <p class="dropzone-hint">PDF, TXT, MD 파일 지원</p>
                  </div>
                  
                  <div v-else class="file-preview">
                    <div class="file-icon">
                      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                        <polyline points="14 2 14 8 20 8"></polyline>
                      </svg>
                    </div>
                    <div class="file-info">
                      <span class="file-name">{{ file.name }}</span>
                      <span class="file-size">{{ (file.size / 1024).toFixed(1) }} KB</span>
                    </div>
                    <button class="file-remove" @click.stop="removeFile">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
              
              <!-- Text Input Area -->
              <div v-if="inputMode === 'text'" class="text-section">
                <textarea
                  v-model="textContent"
                  class="text-input"
                  placeholder="요구사항 문서 내용을 입력하세요..."
                  rows="10"
                ></textarea>
                <button class="sample-btn" @click="useSample">
                  샘플 요구사항 사용
                </button>
              </div>
              
              <!-- Error Display -->
              <div v-if="error" class="error-message">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"></circle>
                  <line x1="12" y1="8" x2="12" y2="12"></line>
                  <line x1="12" y1="16" x2="12.01" y2="16"></line>
                </svg>
                {{ error }}
              </div>
            </template>
          </div>
          
          <!-- Footer (hidden during confirm) -->
          <div v-if="!showClearConfirm" class="modal-footer">
            <button class="btn btn--secondary" @click="closeModal">
              취소
            </button>
            <button 
              class="btn btn--primary"
              :disabled="!canSubmit || isUploading"
              @click="handleStartClick"
            >
              <template v-if="isUploading">
                <span class="spinner"></span>
                업로드 중...
              </template>
              <template v-else>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                분석 시작
              </template>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
  
  <!-- Floating Progress Panel (Bottom-Right, Non-blocking) -->
  <Teleport to="body">
    <Transition name="slide-up">
      <div v-if="showFloatingPanel" class="floating-panel" :class="{ 'is-minimized': isPanelMinimized }">
        <!-- Panel Header -->
        <div class="floating-panel__header" @click="toggleMinimize">
          <div class="floating-panel__title">
            <div class="floating-panel__status" :class="{ 'is-complete': summary, 'is-error': error }">
              <span v-if="isProcessing && !error" class="status-spinner"></span>
              <svg v-else-if="summary" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <svg v-else-if="error" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="15" y1="9" x2="9" y2="15"></line>
                <line x1="9" y1="9" x2="15" y2="15"></line>
              </svg>
            </div>
            <span class="floating-panel__label">
              {{ summary ? '생성 완료' : error ? '오류 발생' : phaseLabel }}
            </span>
            <span v-if="!summary && !error" class="floating-panel__percent">{{ progress }}%</span>
          </div>
          <div class="floating-panel__actions">
            <button class="panel-btn" @click.stop="toggleMinimize" :title="isPanelMinimized ? '펼치기' : '접기'">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline v-if="isPanelMinimized" points="18 15 12 9 6 15"></polyline>
                <polyline v-else points="6 9 12 15 18 9"></polyline>
              </svg>
            </button>
            <button class="panel-btn" @click.stop="closeFloatingPanel" title="닫기">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>
        
        <!-- Progress Bar (always visible in header area) -->
        <div v-if="isProcessing && !isPanelMinimized" class="floating-panel__progress">
          <div class="mini-progress-bar">
            <div class="mini-progress-fill" :style="{ width: `${progress}%` }"></div>
          </div>
          <p class="floating-panel__message">{{ currentMessage }}</p>
        </div>
        
        <!-- Panel Body (collapsible) -->
        <div v-if="!isPanelMinimized" class="floating-panel__body">
          <!-- Live Created Items -->
          <div v-if="isProcessing" class="mini-items">
            <TransitionGroup name="item-list">
              <div 
                v-for="item in createdItems.slice(-5)"
                :key="item.id"
                class="mini-item"
              >
                <span class="item-icon" :class="getTypeClass(item.type)">
                  {{ getTypeIcon(item.type) }}
                </span>
                <span class="mini-item__name">{{ item.name }}</span>
              </div>
            </TransitionGroup>
            <div v-if="createdItems.length > 5" class="mini-items__more">
              +{{ createdItems.length - 5 }} items
            </div>
          </div>
          
          <!-- Summary View -->
          <div v-if="summary" class="mini-summary">
            <div class="mini-summary__stats">
              <div class="mini-stat">
                <span class="mini-stat__icon stat-icon--userstory">US</span>
                <span class="mini-stat__value">{{ summary.user_stories || 0 }}</span>
              </div>
              <div class="mini-stat">
                <span class="mini-stat__icon stat-icon--bc">BC</span>
                <span class="mini-stat__value">{{ summary.bounded_contexts }}</span>
              </div>
              <div class="mini-stat">
                <span class="mini-stat__icon stat-icon--aggregate">A</span>
                <span class="mini-stat__value">{{ summary.aggregates }}</span>
              </div>
              <div class="mini-stat">
                <span class="mini-stat__icon stat-icon--command">C</span>
                <span class="mini-stat__value">{{ summary.commands }}</span>
              </div>
              <div class="mini-stat">
                <span class="mini-stat__icon stat-icon--event">E</span>
                <span class="mini-stat__value">{{ summary.events }}</span>
              </div>
              <div class="mini-stat">
                <span class="mini-stat__icon stat-icon--policy">P</span>
                <span class="mini-stat__value">{{ summary.policies }}</span>
              </div>
            </div>
            <p class="mini-summary__hint">네비게이터에서 확인하세요</p>
          </div>
          
          <!-- Error -->
          <div v-if="error && !isProcessing" class="mini-error">
            {{ error }}
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* ============================================
   Upload Modal Styles
   ============================================ */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-container {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 560px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  border-bottom: 1px solid var(--color-border);
}

.modal-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--color-text-bright);
  margin: 0;
}

.modal-title svg {
  color: var(--color-accent);
}

.modal-close {
  background: transparent;
  border: none;
  color: var(--color-text-light);
  cursor: pointer;
  padding: var(--spacing-xs);
  border-radius: var(--radius-sm);
  transition: background 0.15s, color 0.15s;
}

.modal-close:hover {
  background: var(--color-bg-tertiary);
  color: var(--color-text-bright);
}

.modal-body {
  flex: 1;
  padding: var(--spacing-lg);
  overflow-y: auto;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--spacing-sm);
  padding: var(--spacing-md) var(--spacing-lg);
  border-top: 1px solid var(--color-border);
}

/* Existing Data Warning */
.existing-data-warning {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  background: rgba(255, 193, 7, 0.1);
  border: 1px solid rgba(255, 193, 7, 0.3);
  border-radius: var(--radius-md);
  color: #ffc107;
  font-size: 0.85rem;
  margin-bottom: var(--spacing-md);
}

/* Clear Confirmation Dialog */
.clear-confirm-dialog {
  text-align: center;
  padding: var(--spacing-lg) 0;
}

.clear-confirm-icon {
  color: #ff6464;
  margin-bottom: var(--spacing-md);
}

.clear-confirm-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--color-text-bright);
  margin-bottom: var(--spacing-sm);
}

.clear-confirm-message {
  color: var(--color-text);
  font-size: 0.9rem;
  margin-bottom: var(--spacing-md);
}

.clear-confirm-stats {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
  justify-content: center;
  margin-bottom: var(--spacing-md);
}

.stat-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: var(--color-bg-tertiary);
  border-radius: 20px;
  font-size: 0.8rem;
}

.stat-chip-label {
  color: var(--color-text-light);
}

.stat-chip-value {
  color: var(--color-text-bright);
  font-weight: 600;
}

.clear-confirm-warning {
  color: #ff6464;
  font-size: 0.85rem;
  margin-bottom: var(--spacing-lg);
}

.clear-confirm-actions {
  display: flex;
  justify-content: center;
  gap: var(--spacing-md);
}

/* Input Tabs */
.input-tabs {
  display: flex;
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-md);
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm);
  background: var(--color-bg-tertiary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text);
  cursor: pointer;
  font-size: 0.875rem;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.tab-btn:hover {
  background: var(--color-bg);
}

.tab-btn.active {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: white;
}

/* Dropzone */
.dropzone {
  border: 2px dashed var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-xl);
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.dropzone:hover,
.dropzone.is-active {
  border-color: var(--color-accent);
  background: rgba(34, 139, 230, 0.05);
}

.dropzone.has-file {
  border-style: solid;
  border-color: var(--color-accent);
  background: rgba(34, 139, 230, 0.05);
}

.dropzone-icon {
  color: var(--color-text-light);
  margin-bottom: var(--spacing-md);
}

.dropzone-text {
  font-size: 0.9rem;
  color: var(--color-text);
  margin-bottom: var(--spacing-xs);
}

.dropzone-hint {
  font-size: 0.8rem;
  color: var(--color-text-light);
}

/* File Preview */
.file-preview {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  text-align: left;
}

.file-icon {
  color: var(--color-accent);
}

.file-info {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.file-name {
  font-size: 0.875rem;
  color: var(--color-text-bright);
  font-weight: 500;
}

.file-size {
  font-size: 0.75rem;
  color: var(--color-text-light);
}

.file-remove {
  background: transparent;
  border: none;
  color: var(--color-text-light);
  cursor: pointer;
  padding: var(--spacing-xs);
  border-radius: var(--radius-sm);
  transition: background 0.15s, color 0.15s;
}

.file-remove:hover {
  background: rgba(255, 100, 100, 0.1);
  color: #ff6464;
}

/* Text Input */
.text-section {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.text-input {
  width: 100%;
  padding: var(--spacing-md);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text);
  font-family: var(--font-mono);
  font-size: 0.8rem;
  line-height: 1.5;
  resize: vertical;
  min-height: 160px;
}

.text-input:focus {
  outline: none;
  border-color: var(--color-accent);
}

.text-input::placeholder {
  color: var(--color-text-light);
}

.sample-btn {
  align-self: flex-start;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-light);
  font-size: 0.75rem;
  padding: var(--spacing-xs) var(--spacing-sm);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.sample-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

/* Error Message */
.error-message {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm);
  background: rgba(255, 100, 100, 0.1);
  border: 1px solid rgba(255, 100, 100, 0.3);
  border-radius: var(--radius-md);
  color: #ff6464;
  font-size: 0.8rem;
  margin-top: var(--spacing-md);
}

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-lg);
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, opacity 0.15s;
}

.btn--secondary {
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

.btn--secondary:hover {
  background: var(--color-bg-tertiary);
}

.btn--primary {
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
  color: white;
}

.btn--primary:hover {
  background: #1c7ed6;
  border-color: #1c7ed6;
}

.btn--danger {
  background: #ff6464;
  border: 1px solid #ff6464;
  color: white;
}

.btn--danger:hover {
  background: #e55555;
  border-color: #e55555;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ============================================
   Floating Panel Styles (Bottom-Right)
   ============================================ */
.floating-panel {
  position: fixed;
  bottom: var(--spacing-lg);
  right: var(--spacing-lg);
  width: 320px;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  z-index: 900;
  overflow: hidden;
  transition: width 0.2s ease, height 0.2s ease;
}

.floating-panel.is-minimized {
  width: 240px;
}

.floating-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--color-bg-tertiary);
  cursor: pointer;
  user-select: none;
}

.floating-panel__title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.floating-panel__status {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-accent);
  color: white;
}

.floating-panel__status.is-complete {
  background: #40c057;
}

.floating-panel__status.is-error {
  background: #ff6464;
}

.status-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.floating-panel__label {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-bright);
}

.floating-panel__percent {
  font-size: 0.75rem;
  color: var(--color-text-light);
  margin-left: auto;
}

.floating-panel__actions {
  display: flex;
  gap: 2px;
}

.panel-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--color-text-light);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.panel-btn:hover {
  background: var(--color-bg);
  color: var(--color-text-bright);
}

.floating-panel__progress {
  padding: var(--spacing-sm) var(--spacing-md);
  border-bottom: 1px solid var(--color-border);
}

.mini-progress-bar {
  height: 4px;
  background: var(--color-bg);
  border-radius: 2px;
  overflow: hidden;
}

.mini-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-accent), var(--color-command));
  border-radius: 2px;
  transition: width 0.3s ease;
}

.floating-panel__message {
  font-size: 0.75rem;
  color: var(--color-text-light);
  margin-top: var(--spacing-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.floating-panel__body {
  padding: var(--spacing-sm) var(--spacing-md);
  max-height: 200px;
  overflow-y: auto;
}

/* Mini Items List */
.mini-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mini-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 4px 8px;
  background: var(--color-bg);
  border-radius: var(--radius-sm);
  animation: slideIn 0.2s ease;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(-10px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.item-icon {
  width: 18px;
  height: 18px;
  border-radius: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.6rem;
  font-weight: 600;
  color: white;
  flex-shrink: 0;
}

.item-icon--userstory { background: #20c997; font-size: 0.5rem; }
.item-icon--boundedcontext { background: var(--color-bc); border: 1.5px solid var(--color-text-light); color: var(--color-text-light); }
.item-icon--aggregate { background: var(--color-aggregate); color: var(--color-bc); }
.item-icon--command { background: var(--color-command); }
.item-icon--event { background: var(--color-event); }
.item-icon--policy { background: var(--color-policy); }

.mini-item__name {
  font-size: 0.75rem;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini-items__more {
  font-size: 0.7rem;
  color: var(--color-text-light);
  text-align: center;
  padding: 4px;
}

/* Mini Summary */
.mini-summary__stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-sm);
}

.mini-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: var(--spacing-xs);
  background: var(--color-bg);
  border-radius: var(--radius-sm);
}

.mini-stat__icon {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.6rem;
  font-weight: 600;
  color: white;
}

.stat-icon--userstory { background: #20c997; font-size: 0.55rem; }
.stat-icon--bc { background: var(--color-bc); border: 1.5px solid var(--color-text-light); color: var(--color-text-light); }
.stat-icon--aggregate { background: var(--color-aggregate); color: var(--color-bc); }
.stat-icon--command { background: var(--color-command); }
.stat-icon--event { background: var(--color-event); }
.stat-icon--policy { background: var(--color-policy); }

.mini-stat__value {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--color-text-bright);
}

.mini-summary__hint {
  font-size: 0.7rem;
  color: var(--color-text-light);
  text-align: center;
}

.mini-error {
  padding: var(--spacing-sm);
  background: rgba(255, 100, 100, 0.1);
  border-radius: var(--radius-sm);
  color: #ff6464;
  font-size: 0.75rem;
}

/* Transitions */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-active .modal-container,
.modal-leave-active .modal-container {
  transition: transform 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .modal-container,
.modal-leave-to .modal-container {
  transform: scale(0.95);
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform 0.3s ease, opacity 0.3s ease;
}

.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(20px);
  opacity: 0;
}

/* Item list transitions */
.item-list-enter-active,
.item-list-leave-active {
  transition: all 0.2s ease;
}

.item-list-enter-from {
  opacity: 0;
  transform: translateX(-10px);
}

.item-list-leave-to {
  opacity: 0;
  transform: translateX(10px);
}
</style>
