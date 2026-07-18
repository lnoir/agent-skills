# Strange Mode: Artifact Mode (Sandbox Playground)

Use this mode when variations are standalone documents, web pages, or assets that can coexist in a single folder without worktree overhead.

## Execution Pattern

### Step 1: Create a Sandbox Directory
Initialize a dedicated directory under your project workspace:
```bash
mkdir -p variations-playground/
```

### Step 2: Configure the Spec
Provide the spawned sub-agents with boundaries:
*   File naming specifications (e.g. `option-1.html`, `option-2.html`).
*   Shared context rules (e.g. design variables, color tokens).
*   Allowed library resources.
*   **Web Artifact Contract** (if building browser-based UI): Include the [Web Artifacts contract](#web-artifacts-htmlcssjs-previews) verbatim in each sub-agent's brief. The sub-agents must know about `data-preview`, `postMessage` registration, and state-class listening — they cannot read this file themselves.

### Step 3: Spawn Sub-Agents (Batched)
Spawn a dedicated sub-agent for each option file.
*   **Batching Warning**: Batch sub-agent launches (e.g. max 3 concurrent) to respect provider concurrency limits and prevent hitting `429` rate limit errors during initialization.
*   **Recovery**: If a sub-agent fails (model unreachable, rate limit), wait for the current batch to complete, then re-spawn failed sub-agents in a fresh batch. Do not retry into an already-full concurrency window.

### Step 4: Build the Comparison Gallery
The Orchestrator must compile a central comparison artifact (e.g. a dashboard `index.html` for web previews, a summary document, or a launch script) loaded with pointers to all option files. This is the Orchestrator's responsibility, not the sub-agents'.

### Step 5: Present and Select
Present the completed comparison gallery or asset list to the user for testing and feedback.

### Step 6: Collapse Non-Survivors
Once the user selects a winner:
1.  Copy the winning option file to its final production destination in the codebase.
2.  Archive or delete the remaining option files and the playground directory as directed by the user.

---

## Web Artifacts (HTML/CSS/JS Previews)

When comparing browser-based UI/UX components or CSS/Canvas animations, implement this **Iframe Preview Spec** using the `data-preview` contract to enable clean side-by-side dashboard rendering. **The Orchestrator must include this entire section in each sub-agent's brief** (Step 2).

### 1. The Interface and Registration Contract
All variations must follow a standardized communication design:
*   **`data-preview` attribute**: Tag the root element of the visual widget with `data-preview` (e.g. `<div class="my-widget" data-preview>`).
*   **State Class Matching**: The preview container will toggle `.state-1`, `.state-2`, and `.state-many` classes on the iframe's `document.body` to coordinate visual count changes.
*   **Metadata Registration**: On load, the page must post a registration package back to the parent:
    ```javascript
    window.parent.postMessage({
      type: 'register-design',
      name: 'Clean Option Title',
      description: 'Short behavior description'
    }, '*');
    ```

### 2. Standalone vs. Embedded Mode (CSS)
Ensure each sub-agent wraps its main component so that it isolates and centers itself when inside an iframe, hiding outer panels:
```css
body.embedded {
  padding: 0 !important; margin: 0 !important;
  height: 100vh !important; width: 100vw !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
  background: transparent !important; overflow: hidden !important;
}
/* Hide all dashboard/console wrappers at root-level */
body.embedded > * {
  display: none !important;
}
/* Whitelist preview container tagged by Orchestrator contract */
body.embedded > [data-preview] {
  display: inline-flex !important;
  position: absolute !important;
  top: 50% !important; left: 50% !important;
  transform: translate(-50%, -50%) !important;
}
```

### 3. Reparenting & State Interception (JS)
Since root-level siblings are hidden, reparent the preview element to the document body on load. Also listen to `'set-agents'` message events to mirror state classes directly onto `document.body`:
```javascript
window.addEventListener('load', () => {
  if (window.self !== window.top) {
    document.body.classList.add('embedded');
    const preview = document.querySelector('[data-preview]');
    if (preview) document.body.appendChild(preview);
  }

  // Register design metadata on load
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({
      type: 'register-design',
      name: document.querySelector('h1')?.innerText || document.title,
      description: document.querySelector('.tagline')?.innerText || 'No description provided.'
    }, '*');
  }
});

// Update body classes for nested state selectors
window.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'set-agents') {
    const state = event.data.count;
    document.body.classList.remove('state-1', 'state-2', 'state-many');
    document.body.classList.add('state-' + state);
  }
});
```

### 4. Origin-Safe Dashboard Receiver
In the comparison gallery dashboard (`index.html`), match iframe references via `contentWindow` comparisons to prevent cross-origin DOM exceptions:
```javascript
window.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'register-design') {
    const iframes = document.querySelectorAll('iframe');
    const matchedFrame = Array.from(iframes).find(f => f.contentWindow === event.source);
    if (matchedFrame) {
      const optionId = matchedFrame.id.split('-')[1]; // opt-1 -> 1
      document.getElementById(`desc-${optionId}`).innerHTML = 
        `<b>${event.data.name}</b>: ${event.data.description}`;
    }
  }
});
```
