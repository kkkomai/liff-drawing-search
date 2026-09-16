
    // === LOGGING ===
    var logEl = document.getElementById('debug-log');
    function log(msg) {
      console.log(msg);
      if (logEl) {
        logEl.textContent = msg + '\\n' + logEl.textContent;
      }
    }
    log('=== JS LOADED ===');

    // === ELEMENTS ===
    var fileInput = document.getElementById('search-image');
    var filePreview = document.getElementById('file-preview');
    var searchBtn = document.getElementById('search-submit');
    var searchStatus = document.getElementById('search-status');
    var editorOverlay = document.getElementById('editor-overlay');
    var editorCanvas = document.getElementById('editor-canvas');
    var editorContainer = document.getElementById('editor-container');

    log('fileInput: ' + !!fileInput);
    log('searchBtn: ' + !!searchBtn);
    log('editorOverlay: ' + !!editorOverlay);

    // === TAB SWITCHING ===
    function initTabs() {
      var tabs = document.querySelectorAll('.tab');
      var contents = document.querySelectorAll('.tab-content');
      log('initTabs: found ' + tabs.length + ' tabs, ' + contents.length + ' contents');

      tabs.forEach(function(tab, index) {
        log('Adding listener to tab: ' + tab.textContent);
        tab.addEventListener('click', function(e) {
          e.preventDefault();
          log('Tab clicked: ' + tab.textContent + ' (index ' + index + ')');
          tabs.forEach(function(t) { t.classList.remove('active'); });
          contents.forEach(function(c) { c.classList.remove('active'); });
          tab.classList.add('active');
          if (contents[index]) { contents[index].classList.add('active'); }
          log('Tab switched to index ' + index);
        });
      });
      log('initTabs COMPLETE');
    }

    // === EDITOR STATE ===
    var editorState = { rotation: 0, scale: 1, offsetX: 0, offsetY: 0, image: null, trimMode: false };

    function showEditor(file) {
      log('showEditor: ' + file.name);
      if (searchStatus) { searchStatus.classList.remove('hidden'); searchStatus.textContent = '編集中: ' + file.name; }

      var reader = new FileReader();
      reader.onload = function(e) {
        var img = new Image();
        img.onload = function() {
          editorState = { rotation: 0, scale: 1, offsetX: 0, offsetY: 0, image: img, trimMode: false };
          editorOverlay.classList.add('active');
          drawEditor();
          log('Editor opened');
        };
        img.src = e.target.result;
      };
      reader.readAsDataURL(file);
    }

    function drawEditor() {
      if (!editorState.image) return;
      var canvas = editorCanvas;
      var ctx = canvas.getContext('2d');
      var container = editorContainer;
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
      ctx.fillStyle = '#111';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.translate(canvas.width / 2 + editorState.offsetX, canvas.height / 2 + editorState.offsetY);
      ctx.rotate(editorState.rotation * Math.PI / 180);
      ctx.scale(editorState.scale, editorState.scale);
      ctx.drawImage(editorState.image, -editorState.image.width / 2, -editorState.image.height / 2);
      ctx.restore();
    }

    // Editor controls
    document.getElementById('rotate-left').addEventListener('click', function() { editorState.rotation -= 90; drawEditor(); log('rotate -90'); });
    document.getElementById('rotate-right').addEventListener('click', function() { editorState.rotation += 90; drawEditor(); log('rotate +90'); });
    document.getElementById('zoom-in').addEventListener('click', function() { editorState.scale *= 1.2; drawEditor(); log('zoom in'); });
    document.getElementById('zoom-out').addEventListener('click', function() { editorState.scale /= 1.2; drawEditor(); log('zoom out'); });
    document.getElementById('reset').addEventListener('click', function() { editorState.rotation = 0; editorState.scale = 1; drawEditor(); log('reset'); });
    document.getElementById('editor-done').addEventListener('click', function() {
      editorOverlay.classList.remove('active');
      log('Editor done');
    });

    // === FILE INPUT ===
    if (fileInput) {
      fileInput.addEventListener('change', function() {
        log('fileInput change: ' + (this.files[0] ? this.files[0].name : 'null'));
        if (this.files[0]) {
          filePreview.textContent = this.files[0].name;
        }
      });
    }

    // === SEARCH BUTTON ===
    if (searchBtn) {
      searchBtn.addEventListener('touchstart', function(e) {
        e.preventDefault();
        searchBtn.style.background = '#e74c3c';
        log('SEARCH touchstart');
        alert('検索ボタンがタップされました');
        setTimeout(function(){ searchBtn.style.background = ''; }, 500);
      }, { passive: false });
      log('searchBtn touchstart listener attached');
    }

    // === INIT ===
    function attachEvents() {
      log('attachEvents called');
      if (searchBtn && !searchBtn._listenerAttached) {
        searchBtn.addEventListener('touchstart', function(e) {
          e.preventDefault();
          searchBtn.style.background = '#e74c3c';
          log('SEARCH touchstart (attachEvents)');
          alert('SEARCH via attachEvents');
          if (fileInput && fileInput.files[0]) {
            showEditor(fileInput.files[0]);
          } else {
            log('no file selected');
          }
          setTimeout(function(){ searchBtn.style.background = ''; }, 500);
        }, { passive: false });
        searchBtn._listenerAttached = true;
        log('searchBtn listener attached via attachEvents');
      }
    }

    async function initLiff() {
      log('initLiff: START');

      // Timeout for liff.init (10s)
      var timeoutPromise = new Promise(function(resolve) {
        setTimeout(function() {
          log('initLiff: liff.init TIMEOUT after 10s', true);
          resolve('timeout');
        }, 10000);
      });

      try {
        log('initLiff: calling liff.init...');
        var result = await Promise.race([
          liff.init({ liffId: '2011376207-0e7oVWOT' }),
          timeoutPromise
        ]);
        if (result !== 'timeout') {
          log('initLiff: liff.init SUCCESS');
        }
      } catch (err) {
        log('initLiff: liff.init ERROR: ' + err.message, true);
      }

      // Always init tabs even if LIFF fails
      log('initLiff: forcing initTabs for debugging');
      initTabs();

      log('initLiff: attaching events...');
      attachEvents();
      log('initLiff: initTabs...');
      initTabs();
      log('initLiff: COMPLETE');
    }

    // Start LIFF after short delay
    setTimeout(function() {
      log('Starting initLiff...');
      initLiff();
    }, 500);
  