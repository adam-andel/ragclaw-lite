<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NInput, NButton, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const { t } = useI18n()

// Ordered to mirror the 6 README Core Features
const featureKeys = [
  'featureClaw',
  'featureSandbox',
  'featureWorkspace',
  'featureSkill',
  'featureMcp',
  'featureRag',
]

// Pre-fill default credentials only on first open; once the user has logged in
// successfully we stop auto-filling (so returning visitors start clean).
const LOGIN_SEEDED_KEY = 'ragclaw_login_seeded'
const username = ref('')
const password = ref('')
if (!localStorage.getItem(LOGIN_SEEDED_KEY)) {
  username.value = 'admin'
  password.value = 'admin123'
}
const loading = ref(false)
const passwordInput = ref<InstanceType<typeof NInput> | null>(null)
const cursorGlow = ref<HTMLElement | null>(null)
const particleCanvas = ref<HTMLCanvasElement | null>(null)

// Mouse-reactive full-screen glow (pure frontend, no libs)
function onMove(e: MouseEvent) {
  const el = cursorGlow.value
  if (el) {
    el.style.left = `${e.clientX}px`
    el.style.top = `${e.clientY}px`
    el.style.opacity = '1'
  }
  // Particles react to mouse
  mouse.x = e.clientX
  mouse.y = e.clientY
}
function onLeave() {
  if (cursorGlow.value) cursorGlow.value.style.opacity = '0'
  mouse.x = -9999
  mouse.y = -9999
}

// ===== Particle starfield canvas =====
interface P { x: number; y: number; vx: number; vy: number; r: number }
let particles: P[] = []
const mouse = { x: -9999, y: -9999 }
let rafId = 0

function initParticles() {
  const canvas = particleCanvas.value
  if (!canvas) return
  const dpr = window.devicePixelRatio || 1
  canvas.width = window.innerWidth * dpr
  canvas.height = window.innerHeight * dpr
  canvas.style.width = window.innerWidth + 'px'
  canvas.style.height = window.innerHeight + 'px'
  const ctx = canvas.getContext('2d')
  if (ctx) ctx.scale(dpr, dpr)

  const count = Math.min(90, Math.floor((window.innerWidth * window.innerHeight) / 16000))
  particles = []
  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * window.innerWidth,
      y: Math.random() * window.innerHeight,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 1.5 + 0.5,
    })
  }
}

function animateParticles() {
  const canvas = particleCanvas.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight)

  const isDark = document.documentElement.classList.contains('dark')
  const dotColor = isDark ? '129, 140, 248' : '99, 102, 241'
  const lineColor = isDark ? '129, 140, 248' : '99, 102, 241'

  // Update + draw particles
  for (const p of particles) {
    p.x += p.vx
    p.y += p.vy
    if (p.x < 0 || p.x > window.innerWidth) p.vx *= -1
    if (p.y < 0 || p.y > window.innerHeight) p.vy *= -1

    // Mouse repulsion
    const dx = p.x - mouse.x
    const dy = p.y - mouse.y
    const dist = Math.sqrt(dx * dx + dy * dy)
    if (dist < 120) {
      const force = (120 - dist) / 120
      p.x += (dx / dist) * force * 1.5
      p.y += (dy / dist) * force * 1.5
    }

    ctx.beginPath()
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(${dotColor}, ${isDark ? 0.8 : 0.6})`
    ctx.fill()
  }

  // Draw connecting lines
  for (let i = 0; i < particles.length; i++) {
    for (let j = i + 1; j < particles.length; j++) {
      const dx = particles[i].x - particles[j].x
      const dy = particles[i].y - particles[j].y
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < 130) {
        const alpha = (1 - dist / 130) * (isDark ? 0.25 : 0.18)
        ctx.beginPath()
        ctx.moveTo(particles[i].x, particles[i].y)
        ctx.lineTo(particles[j].x, particles[j].y)
        ctx.strokeStyle = `rgba(${lineColor}, ${alpha})`
        ctx.lineWidth = 0.5
        ctx.stroke()
      }
    }
    // Lines to mouse
    const dxm = particles[i].x - mouse.x
    const dym = particles[i].y - mouse.y
    const distM = Math.sqrt(dxm * dxm + dym * dym)
    if (distM < 180) {
      const alpha = (1 - distM / 180) * (isDark ? 0.4 : 0.3)
      ctx.beginPath()
      ctx.moveTo(particles[i].x, particles[i].y)
      ctx.lineTo(mouse.x, mouse.y)
      ctx.strokeStyle = `rgba(${lineColor}, ${alpha})`
      ctx.lineWidth = 0.6
      ctx.stroke()
    }
  }

  rafId = requestAnimationFrame(animateParticles)
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  initParticles()
  animateParticles()
  window.addEventListener('resize', initParticles)
})
onUnmounted(() => {
  cancelAnimationFrame(rafId)
  window.removeEventListener('resize', initParticles)
})

async function handleLogin() {
  if (loading.value) return
  // Error prevention: validate before hitting the network
  if (!username.value.trim()) {
    message.warning(t('login.enterUsername'))
    return
  }
  if (!password.value) {
    message.warning(t('login.enterPassword'))
    await nextTick()
    passwordInput.value?.focus()
    return
  }
  loading.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    localStorage.setItem(LOGIN_SEEDED_KEY, '1')
    message.success(t('login.loginSuccess'))
    router.push('/chat')
  } catch (e: any) {
    // Visibility of system status: surface the server/network error to the user
    message.error(e?.message || t('login.loginFailed'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page" @mousemove="onMove" @mouseleave="onLeave">
    <!-- Full-screen dynamic background (pure frontend) -->
    <div class="bg-aurora" aria-hidden="true">
      <span class="aurora a1" />
      <span class="aurora a2" />
      <span class="aurora a3" />
    </div>
    <div class="bg-grid" aria-hidden="true" />
    <canvas ref="particleCanvas" class="bg-particles" aria-hidden="true" />
    <div ref="cursorGlow" class="bg-cursor" aria-hidden="true" />

    <div class="login-shell" role="presentation">
      <!-- Brand / value panel (hidden on small screens) -->
      <aside class="brand-panel" aria-hidden="true">
        <div class="brand-content">
          <div class="brand-logo">RAGClaw</div>
          <p class="brand-subtitle">{{ t('login.brandSubtitle') }}</p>
          <ul class="brand-features">
            <li v-for="key in featureKeys" :key="key">
              <span class="dot" />{{ t('login.' + key) }}
            </li>
          </ul>
        </div>
        <p class="brand-footer">RAGClaw · Lite</p>
      </aside>

      <!-- Form panel -->
      <section class="form-panel">
        <div class="form-brand">
          <span class="form-brand-mark">R</span>
          <span class="form-brand-name">RAGClaw</span>
        </div>
        <div class="login-header">
          <h1 class="login-title">{{ t('login.welcomeBack') }}</h1>
          <p class="login-subtitle">{{ t('login.welcomeSubtitle') }}</p>
        </div>

        <form class="login-form" @submit.prevent="handleLogin" novalidate>
          <div class="field">
            <label for="login-username" class="field-label">{{ t('login.username') }}</label>
            <NInput
              v-model:value="username"
              :placeholder="t('login.usernamePlaceholder')"
              size="large"
              :input-props="{
                id: 'login-username',
                autocomplete: 'username',
                autocapitalize: 'off',
                autocorrect: 'off',
                spellcheck: false,
              }"
            />
          </div>

          <div class="field">
            <label for="login-password" class="field-label">{{ t('login.password') }}</label>
            <NInput
              ref="passwordInput"
              v-model:value="password"
              type="password"
              show-password-on="click"
              :placeholder="t('login.passwordPlaceholder')"
              size="large"
              :input-props="{ id: 'login-password', autocomplete: 'current-password' }"
            />
          </div>

          <NButton
            type="primary"
            size="large"
            block
            attr-type="submit"
            :loading="loading"
            :disabled="loading"
          >
            {{ loading ? t('login.loggingIn') : t('login.login') }}
          </NButton>
        </form>

        <p class="login-hint">
          <span class="hint-icon" aria-hidden="true">🔒</span>
          {{ t('login.noAccountHint') }}
        </p>

        <p class="login-copy">© RAGClaw</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* ===== Theme-aware dynamic-background tokens ===== */
.login-page {
  --aurora-1: rgba(59, 130, 246, 0.42);
  --aurora-2: rgba(139, 92, 246, 0.36);
  --aurora-3: rgba(14, 165, 183, 0.30);
  --grid-line: rgba(99, 102, 241, 0.10);
  --cursor-glow: rgba(99, 102, 241, 0.20);

  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  overflow: hidden;
  background: #0a0e27;
  isolation: isolate;
}
html:not(.dark) .login-page {
  background: #eef2fb;
  --grid-line: rgba(49, 46, 129, 0.12);
}
html.dark .login-page {
  --aurora-1: rgba(59, 130, 246, 0.50);
  --aurora-2: rgba(139, 92, 246, 0.42);
  --aurora-3: rgba(14, 165, 183, 0.36);
  --grid-line: rgba(129, 140, 248, 0.10);
  --cursor-glow: rgba(129, 140, 248, 0.26);
}

/* ===== Layer 1: Full-screen aurora blobs ===== */
.bg-aurora {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
}
.bg-aurora .aurora {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  will-change: transform;
  mix-blend-mode: screen;
}
.bg-aurora .a1 {
  width: 55vw; height: 55vw;
  top: -16vw; left: -12vw;
  background: var(--aurora-1);
  animation: aurora-drift-1 18s ease-in-out infinite;
}
.bg-aurora .a2 {
  width: 45vw; height: 45vw;
  bottom: -18vw; right: -10vw;
  background: var(--aurora-2);
  animation: aurora-drift-2 22s ease-in-out infinite;
}
.bg-aurora .a3 {
  width: 38vw; height: 38vw;
  top: 36%; left: 48%;
  background: var(--aurora-3);
  animation: aurora-drift-3 26s ease-in-out infinite;
}
@keyframes aurora-drift-1 {
  0%   { transform: translate(0, 0) scale(1) rotate(0deg); }
  33%  { transform: translate(12vw, 10vh) scale(1.2) rotate(120deg); }
  66%  { transform: translate(-6vw, 14vh) scale(0.9) rotate(240deg); }
  100% { transform: translate(0, 0) scale(1) rotate(360deg); }
}
@keyframes aurora-drift-2 {
  0%   { transform: translate(0, 0) scale(1) rotate(0deg); }
  40%  { transform: translate(-14vw, -8vh) scale(1.15) rotate(-100deg); }
  100% { transform: translate(0, 0) scale(1) rotate(-360deg); }
}
@keyframes aurora-drift-3 {
  0%   { transform: translate(0, 0) scale(1); }
  50%  { transform: translate(-10vw, 12vh) scale(1.25); }
  100% { transform: translate(0, 0) scale(1); }
}

/* ===== Layer 2: Tech grid (visible everywhere, no center fade) ===== */
.bg-grid {
  position: fixed;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background-image:
    linear-gradient(var(--grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-line) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse 90% 90% at 50% 50%, #000 30%, transparent 100%);
  -webkit-mask-image: radial-gradient(ellipse 90% 90% at 50% 50%, #000 30%, transparent 100%);
}

/* ===== Layer 2b: Particle starfield canvas ===== */
.bg-particles {
  position: fixed;
  inset: 0;
  z-index: 1;
  pointer-events: none;
}

/* ===== Layer 3: Mouse-reactive glow (above card, screen-blend) ===== */
.bg-cursor {
  position: fixed;
  left: 50%;
  top: 50%;
  width: 600px;
  height: 600px;
  z-index: 4;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
  opacity: 0;
  background: radial-gradient(circle, var(--cursor-glow) 0%, transparent 60%);
  mix-blend-mode: screen;
  transition: left 0.4s cubic-bezier(0.22, 1, 0.36, 1),
              top 0.4s cubic-bezier(0.22, 1, 0.36, 1),
              opacity 0.4s ease;
}

/* ===== Login shell (pure transparent frosted glass, no own background color) ===== */
.login-shell {
  position: relative;
  z-index: 2;
  display: flex;
  width: 920px;
  max-width: 100%;
  min-height: 560px;
  background: transparent;
  backdrop-filter: blur(20px) saturate(1.4);
  -webkit-backdrop-filter: blur(20px) saturate(1.4);
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 20px;
  overflow: hidden;
  box-shadow:
    0 1px 0 0 rgba(255, 255, 255, 0.2) inset,
    0 20px 60px -20px rgba(0, 0, 0, 0.15);
}
html.dark .login-shell {
  border-color: rgba(255, 255, 255, 0.10);
  box-shadow:
    0 1px 0 0 rgba(255, 255, 255, 0.06) inset,
    0 20px 60px -20px rgba(0, 0, 0, 0.4);
}
/* Accent edge line on top of the card */
.login-shell::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--color-primary, #6366f1), var(--color-secondary, #8b5cf6), var(--color-accent, #06b6d4), transparent);
  opacity: 0.8;
  z-index: 3;
}

/* ===== Brand panel (transparent, no own background — fully fused with bg) ===== */
.brand-panel {
  position: relative;
  flex: 1.15;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 40px 44px;
  color: #fff;
  background: transparent;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
/* Remove old dot-grid and blob — let the global background show through */
.brand-panel::before,
.brand-panel::after {
  display: none;
  content: none;
}
.brand-content { position: relative; z-index: 1; }
.brand-logo {
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: 0.5px;
  margin-bottom: 16px;
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
}
.brand-subtitle {
  font-size: 1.12rem;
  font-weight: 600;
  line-height: 1.55;
  opacity: 1;
  margin-bottom: 26px;
  max-width: 340px;
  text-shadow: 0 1px 8px rgba(0, 0, 0, 0.25);
}
.brand-features {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.brand-features li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: 0.9rem;
  line-height: 1.5;
  opacity: 0.92;
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.2);
}
.brand-features .dot {
  flex-shrink: 0;
  width: 8px; height: 8px;
  margin-top: 7px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.18);
}
.brand-footer {
  position: relative;
  z-index: 1;
  margin-top: 28px;
  font-size: 0.78rem;
  opacity: 0.7;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.2);
}

/* ===== Form panel (transparent, no own background) ===== */
.form-panel {
  flex-shrink: 0;
  width: 420px;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 48px 44px;
  background: transparent;
}
.form-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}
.form-brand-mark {
  width: 42px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  font-weight: 800;
  font-size: 1.25rem;
  color: #fff;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  box-shadow: 0 8px 18px -8px rgba(99, 102, 241, 0.55);
}
.form-brand-name {
  font-size: 1.1rem;
  font-weight: 700;
  letter-spacing: 0.4px;
  color: #fff;
  text-shadow: 0 1px 8px rgba(0, 0, 0, 0.3);
}
html:not(.dark) .form-brand-name {
  color: #1e293b;
  text-shadow: none;
}
.login-header { margin-bottom: 26px; }
.login-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: #fff;
  margin-bottom: 6px;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
}
html:not(.dark) .login-title {
  color: #1e293b;
  text-shadow: none;
}
.login-subtitle {
  font-size: 0.9rem;
  color: rgba(255, 255, 255, 0.75);
}
html:not(.dark) .login-subtitle {
  color: #64748b;
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.field { display: flex; flex-direction: column; gap: 8px; }
.field-label {
  font-size: 0.82rem;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.85);
}
html:not(.dark) .field-label {
  color: #334155;
}
/* Refine naive inputs to match the glass design */
.login-form :deep(.n-input) {
  border-radius: 10px;
}
.login-form :deep(.n-input .n-input__input),
.login-form :deep(.n-input .n-input__textarea-el) {
  background: rgba(255, 255, 255, 0.08);
  border-radius: 10px;
}
html:not(.dark) .login-form :deep(.n-input .n-input__input),
html:not(.dark) .login-form :deep(.n-input .n-input__textarea-el) {
  background: rgba(255, 255, 255, 0.5);
}
.login-form :deep(.n-button--primary) {
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  box-shadow: 0 10px 22px -10px rgba(99, 102, 241, 0.65);
  transition: transform 150ms ease,
              box-shadow 150ms ease;
}
.login-form :deep(.n-button--primary:hover) {
  transform: translateY(-1px);
  box-shadow: 0 14px 26px -10px rgba(99, 102, 241, 0.75);
}
.login-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 22px;
  font-size: 0.8rem;
  color: rgba(255, 255, 255, 0.6);
}
html:not(.dark) .login-hint {
  color: #64748b;
}
.hint-icon { font-size: 0.85rem; }
.login-copy {
  margin-top: 28px;
  text-align: center;
  font-size: 0.72rem;
  color: rgba(255, 255, 255, 0.5);
}
html:not(.dark) .login-copy {
  color: #94a3b8;
}

/* ===== Responsive ===== */
@media (max-width: 860px) {
  .login-shell {
    width: 100%;
    min-height: auto;
    border-radius: 14px;
  }
  .brand-panel { display: none; }
  .form-panel {
    width: 100%;
    padding: 40px 28px;
  }
}
@media (max-width: 480px) {
  .login-page { padding: 0; }
  .login-shell {
    border-radius: 0;
    border: none;
    box-shadow: none;
    min-height: 100vh;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
  .form-panel { padding: 32px 22px; justify-content: center; }
}

/* Respect reduced-motion preferences */
@media (prefers-reduced-motion: reduce) {
  .bg-aurora .aurora {
    animation: none !important;
  }
  .bg-cursor { transition: opacity 0.3s ease; }
}
</style>
