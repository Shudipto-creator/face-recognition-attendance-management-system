
/* Paste/Bundle your creature implementation ABOVE this bootstrap.
   Requirements from creature code:
   - exposes a constructor `Creature(...)`
   - creature.follow(x,y) and creature.draw(true/false)
   - uses a global `ctx` CanvasRenderingContext2D OR accepts ctx internally
*/

// --- Cursor bootstrap (overlay canvas) ---
(function () {
  if (window.__creatureCursorStarted) return;
  window.__creatureCursorStarted = true;

  const Input = (window.Input ||= { keys: [], mouse: { left: false, right: false, middle: false, x: 0, y: 0 } });

  // fix mouse buttons + track
  document.addEventListener("mousedown", (e) => {
    if (e.button === 0) Input.mouse.left = true;
    if (e.button === 1) Input.mouse.middle = true;
    if (e.button === 2) Input.mouse.right = true;
  });
  document.addEventListener("mouseup", (e) => {
    if (e.button === 0) Input.mouse.left = false;
    if (e.button === 1) Input.mouse.middle = false;
    if (e.button === 2) Input.mouse.right = false;
  });
  document.addEventListener("mousemove", (e) => {
    Input.mouse.x = e.clientX;
    Input.mouse.y = e.clientY;
  }, { passive: true });

  const canvas = document.createElement("canvas");
  const ctx2d = canvas.getContext("2d");

  canvas.style.position = "fixed";
  canvas.style.left = "0";
  canvas.style.top = "0";
  canvas.style.width = "100vw";
  canvas.style.height = "100vh";
  canvas.style.pointerEvents = "none";
  canvas.style.zIndex = "2147483647";
  canvas.style.userSelect = "none";
  document.documentElement.appendChild(canvas);

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(window.innerWidth * dpr);
    canvas.height = Math.round(window.innerHeight * dpr);
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resize);
  resize();

  // if creature code relies on a global `ctx`, provide it:
  window.ctx = ctx2d;

  // Construct creature (adjust args to match your real constructor defaults)
  if (typeof window.Creature !== "function") {
    // creature code not bundled; fail silently
    return;
  }
  const creature = new window.Creature(
    window.innerWidth / 2,
    window.innerHeight / 2,
    0,
    0.2, 0.8, 0.9, 0.01,
    0.2, 0.8, 0.9, 0.01
  );

  function tick() {
    ctx2d.clearRect(0, 0, window.innerWidth, window.innerHeight);
    creature.follow(Input.mouse.x, Input.mouse.y);
    creature.draw(true);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();