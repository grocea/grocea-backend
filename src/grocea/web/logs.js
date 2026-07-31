const feedUrl = new URL('/api/dev/logs', window.location.origin)
const levels = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 }

const elements = {
  connectionStatus: document.querySelector('#connection-status'),
  emptyState: document.querySelector('#empty-state'),
  error: document.querySelector('#feed-error'),
  list: document.querySelector('#log-list'),
  pauseButton: document.querySelector('#pause-button'),
  refreshTime: document.querySelector('#refresh-time'),
  search: document.querySelector('#search-filter'),
  severity: document.querySelector('#severity-filter'),
  summary: document.querySelector('#log-summary'),
}

const state = {
  capacity: 500,
  entries: [],
  inFlight: false,
  latestId: null,
  paused: false,
  streamId: null,
}

function appendTextElement(parent, tagName, className, text) {
  const element = document.createElement(tagName)
  if (className) element.className = className
  element.textContent = text
  parent.append(element)
  return element
}

function searchableText(entry) {
  const http = entry.http ?? {}
  return [
    entry.level,
    entry.source,
    entry.message,
    entry.request_id,
    http.method,
    http.path,
    http.status_code,
  ]
    .filter((value) => value !== undefined && value !== null)
    .join(' ')
    .toLowerCase()
}

function filteredEntries() {
  const minimumLevel = Number(elements.severity.value)
  const query = elements.search.value.trim().toLowerCase()
  return state.entries.filter((entry) => {
    const meetsSeverity = (levels[entry.level] ?? 0) >= minimumLevel
    return meetsSeverity && (!query || searchableText(entry).includes(query))
  })
}

function addMetaChip(parent, text) {
  appendTextElement(parent, 'span', 'meta-chip', text)
}

function renderEntry(entry) {
  const item = document.createElement('li')
  item.className = 'log-entry'
  item.dataset.level = entry.level

  const header = document.createElement('div')
  header.className = 'log-entry-header'
  const time = appendTextElement(header, 'time', '', new Date(entry.timestamp).toLocaleString())
  time.dateTime = entry.timestamp
  appendTextElement(header, 'span', 'level-badge', entry.level)
  appendTextElement(header, 'span', 'log-source', entry.source)
  item.append(header)

  appendTextElement(item, 'p', 'log-message', entry.message)

  const meta = document.createElement('div')
  meta.className = 'log-entry-meta'
  if (entry.http) {
    addMetaChip(meta, entry.http.method)
    addMetaChip(meta, entry.http.path)
    addMetaChip(meta, String(entry.http.status_code))
    addMetaChip(meta, `${entry.http.duration_ms.toFixed(1)}ms`)
  }
  if (entry.request_id) addMetaChip(meta, `request ${entry.request_id}`)
  if (meta.childElementCount > 0) item.append(meta)

  if (entry.traceback) {
    const details = document.createElement('details')
    details.className = 'traceback'
    appendTextElement(details, 'summary', '', 'Traceback')
    appendTextElement(details, 'pre', '', entry.traceback)
    item.append(details)
  }
  return item
}

function render() {
  const entries = filteredEntries()
  const nodes = [...entries].reverse().map(renderEntry)
  elements.list.replaceChildren(...nodes)
  elements.emptyState.hidden = entries.length !== 0
  elements.summary.textContent = `${entries.length} shown · ${state.entries.length} retained · capacity ${state.capacity}`
}

function setConnectionStatus(label, status) {
  elements.connectionStatus.textContent = label
  elements.connectionStatus.dataset.state = status
}

function mergeEntries(incoming, resetRequired) {
  if (state.streamId === null || resetRequired) {
    state.entries = incoming
  } else {
    const knownIds = new Set(state.entries.map((entry) => entry.id))
    state.entries.push(...incoming.filter((entry) => !knownIds.has(entry.id)))
  }
  state.entries = state.entries.slice(-state.capacity)
}

async function refreshLogs() {
  if (state.paused || state.inFlight) return
  state.inFlight = true
  setConnectionStatus('Refreshing', 'loading')
  const requestUrl = new URL(feedUrl)
  if (state.streamId !== null && state.latestId !== null) {
    requestUrl.searchParams.set('stream_id', state.streamId)
    requestUrl.searchParams.set('after_id', String(state.latestId))
  }

  try {
    const response = await fetch(requestUrl, { cache: 'no-store', headers: { Accept: 'application/json' } })
    if (!response.ok) throw new Error(`Log feed returned ${response.status}`)
    const payload = await response.json()
    state.capacity = payload.capacity
    mergeEntries(payload.entries, payload.reset_required || payload.stream_id !== state.streamId)
    state.streamId = payload.stream_id
    state.latestId = payload.latest_id
    elements.error.hidden = true
    elements.refreshTime.textContent = `Updated ${new Date().toLocaleTimeString()}`
    setConnectionStatus('Live', 'live')
    render()
  } catch (error) {
    elements.error.textContent = error instanceof Error ? error.message : 'Unable to load API logs.'
    elements.error.hidden = false
    setConnectionStatus('Disconnected', 'error')
  } finally {
    state.inFlight = false
  }
}

elements.pauseButton.addEventListener('click', () => {
  state.paused = !state.paused
  elements.pauseButton.textContent = state.paused ? 'Resume' : 'Pause'
  if (state.paused) {
    setConnectionStatus('Paused', 'paused')
  } else {
    void refreshLogs()
  }
})

elements.severity.addEventListener('change', render)
elements.search.addEventListener('input', render)

void refreshLogs()
window.setInterval(() => void refreshLogs(), 2000)
