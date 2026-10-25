// ── Motion helpers shared by all Jar pages ──────────────────────
// Small, dependency-free. Every helper degrades to an instant change
// when the user prefers reduced motion.

const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const EASE_OUT = 'cubic-bezier(0.165, 0.84, 0.44, 1)';

// Sliding pill that follows the active nav tab
function initNavPill() {
    const tabs = document.querySelector('nav .tabs');
    if (!tabs) return;

    const pill = document.createElement('span');
    pill.className = 'nav-pill no-anim';
    tabs.prepend(pill);
    tabs.classList.add('has-pill');

    function move() {
        const active = tabs.querySelector('.tab.active');
        if (!active || active.offsetParent === null) { pill.style.opacity = '0'; return; }
        pill.style.opacity = '1';
        pill.style.width = active.offsetWidth + 'px';
        pill.style.transform = 'translateX(' + active.offsetLeft + 'px)';
    }

    move();
    // Enable the spring only after the first placement so the pill doesn't fly in on load
    requestAnimationFrame(() => requestAnimationFrame(() => pill.classList.remove('no-anim')));

    const observer = new MutationObserver(move);
    tabs.querySelectorAll('.tab').forEach(tab => {
        observer.observe(tab, { attributes: true, attributeFilter: ['class', 'style'] });
    });
    window.addEventListener('resize', move);
    if (document.fonts) document.fonts.ready.then(move);
}

// Count a number up from zero, formatting each frame
function countUp(el, to, format, duration) {
    format = format || String;
    duration = duration || 900;
    if (prefersReducedMotion || !isFinite(to)) { el.textContent = format(to); return; }
    const start = performance.now();
    const easeOutExpo = t => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));
    let done = false;
    function frame(now) {
        if (done) return;
        const t = Math.min(1, (now - start) / duration);
        el.textContent = format(to * easeOutExpo(t));
        if (t < 1) requestAnimationFrame(frame);
        else done = true;
    }
    requestAnimationFrame(frame);
    // rAF is paused in background tabs; never leave the number blank
    setTimeout(() => { if (!done) { done = true; el.textContent = format(to); } }, duration + 100);
}

// Slide an element away, then close the gap it leaves behind
async function collapseOut(el) {
    if (prefersReducedMotion) { el.remove(); return; }
    const cs = getComputedStyle(el);
    const gap = parseFloat(getComputedStyle(el.parentElement).rowGap) || 0;

    await el.animate(
        [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(28px) scale(0.98)' }],
        { duration: 200, easing: EASE_OUT, fill: 'forwards' }
    ).finished;

    el.style.overflow = 'hidden';
    await el.animate(
        [
            { height: el.offsetHeight + 'px', paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, marginBottom: '0px' },
            { height: '0px', paddingTop: '0px', paddingBottom: '0px', marginBottom: -gap + 'px', borderWidth: '0px' },
        ],
        { duration: 260, easing: EASE_OUT, fill: 'forwards' }
    ).finished;
    el.remove();
}

// Restart a CSS animation class on an element
function replayAnimation(el, className) {
    el.classList.remove(className);
    void el.offsetWidth;
    el.classList.add(className);
}

// Modal backdrop open/close with pop-in / pop-out
function openModal(backdrop) {
    clearTimeout(backdrop._closeTimer);
    backdrop.classList.remove('is-closing');
    backdrop.style.display = '';
}

function closeModal(backdrop) {
    if (backdrop.style.display === 'none') return;
    backdrop.classList.add('is-closing');
    backdrop._closeTimer = setTimeout(() => {
        backdrop.style.display = 'none';
        backdrop.classList.remove('is-closing');
    }, prefersReducedMotion ? 0 : 180);
}

// Toast with spring entrance and soft exit
function popToast(toast, msg, isError) {
    clearTimeout(toast._timer);
    clearTimeout(toast._hideTimer);
    toast.textContent = msg;
    toast.classList.toggle('toast-error', !!isError);
    toast.classList.remove('is-leaving');
    toast.style.display = 'none';
    void toast.offsetWidth;
    toast.style.display = '';
    toast._timer = setTimeout(() => {
        toast.classList.add('is-leaving');
        toast._hideTimer = setTimeout(() => { toast.style.display = 'none'; }, 180);
    }, 2800);
}

// Shake an element to signal invalid input
function shake(el) {
    if (prefersReducedMotion) return;
    replayAnimation(el, 'shake');
}

initNavPill();
