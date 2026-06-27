function submitManualUpload() {
  var formData = new FormData();
  formData.append('role', document.getElementById('uploadRole').value);

  var files = document.getElementById('uploadFiles').files;
  for (var i = 0; i < files.length; i++) {
    formData.append('images', files[i]);
  }

  var urlText = (document.getElementById('uploadUrls').value || '').trim();
  if (urlText) {
    urlText.split('\n').forEach(function(line) {
      line = line.trim();
      if (line) formData.append('urls', line);
    });
  }

  if (!files.length && !urlText) {
    document.getElementById('uploadResult').innerHTML = '<span class="text-warning">请选择文件或输入 URL</span>';
    return;
  }

  var resultEl = document.getElementById('uploadResult');
  resultEl.innerHTML = '<span class="text-info">⏳ 上传中...</span>';

  fetch('/ozon/api/source-media/{{ source.id }}/upload', {
    method: 'POST',
    body: formData
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.ok) {
      resultEl.innerHTML = '<span class="text-success">✅ 已上传 ' + data.count + ' 张图片</span>';
      setTimeout(function() { location.reload(); }, 1500);
    } else {
      resultEl.innerHTML = '<span class="text-danger">❌ ' + (data.error || '上传失败') + '</span>';
    }
  })
  .catch(function(e) {
    resultEl.innerHTML = '<span class="text-danger">❌ 请求失败: ' + e.message + '</span>';
