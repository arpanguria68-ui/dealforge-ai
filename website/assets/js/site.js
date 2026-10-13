/* DealForge project site: lightbox, copy buttons, agent filters. No network calls. */
(function () {
  var lb = document.createElement('div');
  lb.className = 'lb';
  lb.innerHTML = '<img alt="">';
  document.body.appendChild(lb);
  lb.addEventListener('click', function () { lb.classList.remove('on'); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') lb.classList.remove('on'); });
  document.querySelectorAll('[data-zoom]').forEach(function (el) {
    el.addEventListener('click', function () {
      var src = el.getAttribute('data-zoom') || el.getAttribute('src');
      lb.querySelector('img').src = src;
      lb.classList.add('on');
    });
  });
  document.querySelectorAll('pre').forEach(function (pre) {
    var b = document.createElement('button');
    b.className = 'copy'; b.type = 'button'; b.textContent = 'Copy';
    b.addEventListener('click', function () {
      var t = pre.innerText.replace(/^Copy\n?/, '');
      if (navigator.clipboard) { navigator.clipboard.writeText(t); }
      b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy'; }, 1500);
    });
    pre.appendChild(b);
  });
  var buttons = document.querySelectorAll('.filter button');
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      buttons.forEach(function (x) { x.classList.remove('on'); });
      btn.classList.add('on');
      var g = btn.getAttribute('data-group');
      document.querySelectorAll('.agent').forEach(function (a) {
        a.style.display = (g === 'all' || a.getAttribute('data-group') === g) ? '' : 'none';
      });
    });
  });
})();
