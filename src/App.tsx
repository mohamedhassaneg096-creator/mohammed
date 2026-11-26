/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Download,
  FileText,
  Film,
  FolderOpen,
  Pause,
  Play,
  RefreshCw,
  Scissors,
  Sliders,
  Smartphone,
  Square,
  Terminal,
  Trash2,
  Upload,
  WifiOff,
  X,
  Zap,
} from 'lucide-react';
import { useOnlineStatus, usePWAInstall } from './hooks/usePWAInstall';
import { decodeAudioFileToBuffer, encodeAudioBufferSliceToWav } from './utils/audioSlice';
import { loadWorkspaceIDB, saveWorkspaceIDB } from './utils/idbStorage';

type Stage = 'inputs' | 'cuts' | 'scenes' | 'motion' | 'export';
type MotionType = 'zoom_in' | 'zoom_out' | 'left' | 'right' | 'snap_zoom';
type ThemePreset = 'blue_night' | 'calm_forest' | 'daylight';
type ResourceMode = 'light' | 'medium' | 'fast';

interface CutSegment {
  id: number;
  text: string;
  matchedText: string;
  start: number;
  end: number;
  confidence: number;
  estimated?: boolean;
  sceneType: 'image' | 'video';
  sceneFile: string;
  videoDuration?: number;
  color: string;
}

interface MotionAssignment {
  type: MotionType;
  x: number;
  y: number;
  strength: number;
  ease: boolean;
  duration?: number;
}

interface SlicedClip {
  segId: number;
  url: string;
  duration: number;
  start: number;
  end: number;
  sizeBytes: number;
}

const THEME_OPTIONS: { id: ThemePreset; label: string }[] = [
  { id: 'blue_night', label: 'ليل أزرق' },
  { id: 'calm_forest', label: 'غابة هادية' },
  { id: 'daylight', label: 'نهار' },
];

const MOTION_TABS: { id: MotionType; label: string; icon: string }[] = [
  { id: 'zoom_in', label: 'Zoom In', icon: '⊕' },
  { id: 'zoom_out', label: 'Zoom Out', icon: '⊖' },
  { id: 'left', label: 'Left', icon: '⇠' },
  { id: 'right', label: 'Right', icon: '⇢' },
  { id: 'snap_zoom', label: 'حركة خاطفة', icon: '⚡' },
];

const RESOURCE_MODES: { id: ResourceMode; label: string; hint: string; cooldownMs: number }[] = [
  { id: 'light', label: 'خفيف', hint: 'موصى به — يريح المعالج والحرارة للملفات الطويلة', cooldownMs: 420 },
  { id: 'medium', label: 'متوسط', hint: 'توازن بين السرعة وهدوء الجهاز', cooldownMs: 180 },
  { id: 'fast', label: 'سريع', hint: 'أقصى سرعة متاحة على الجهاز', cooldownMs: 40 },
];

const INITIAL_SCRIPT = `1. في قلب الأندلس بدأت قصة حضارة مضيئة
2. كانت قرطبة مركزاً للعلم والعمارة والفنون
3. وتدفقت المياه في حدائق قصر الحمراء
4. وازدهرت المكتبات والمدارس في كل مدينة
5. وبقي أثر تلك الحقبة خالداً حتى يومنا هذا`;

const INITIAL_SEGMENTS: CutSegment[] = [
  {
    id: 1,
    text: 'في قلب الأندلس بدأت قصة حضارة مضيئة',
    matchedText: 'في قلب الأندلس بدات قصه',
    start: 0.0,
    end: 2.1,
    confidence: 96,
    sceneType: 'image',
    sceneFile: '1.png',
    color: 'linear-gradient(135deg, #2b3a55 0%, #1d2638 100%)',
  },
  {
    id: 2,
    text: 'كانت قرطبة مركزاً للعلم والعمارة والفنون',
    matchedText: 'كانت قرطبه مركزا للعلم',
    start: 2.1,
    end: 4.3,
    confidence: 92,
    sceneType: 'image',
    sceneFile: '02 - قرطبة.png',
    color: 'linear-gradient(135deg, #1f4037 0%, #182b26 100%)',
  },
  {
    id: 3,
    text: 'وتدفقت المياه في حدائق قصر الحمراء',
    matchedText: 'وتدفقت المياه في حدائق',
    start: 4.3,
    end: 6.8,
    confidence: 88,
    estimated: true,
    sceneType: 'video',
    sceneFile: 'scene_3.mp4',
    videoDuration: 0.8,
    color: 'linear-gradient(135deg, #3b2d54 0%, #201a2e 100%)',
  },
  {
    id: 4,
    text: 'وازدهرت المكتبات والمدارس في كل مدينة',
    matchedText: 'وازدهرت المكتبات والمدارس',
    start: 6.8,
    end: 8.6,
    confidence: 61,
    sceneType: 'image',
    sceneFile: '4.png',
    color: 'linear-gradient(135deg, #4b3a24 0%, #261e14 100%)',
  },
  {
    id: 5,
    text: 'وبقي أثر تلك الحقبة خالداً حتى يومنا هذا',
    matchedText: 'وبقي اثر تلك الحقبه',
    start: 8.6,
    end: 10.5,
    confidence: 94,
    sceneType: 'image',
    sceneFile: '5.png',
    color: 'linear-gradient(135deg, #253b4a 0%, #16222b 100%)',
  },
];

function fmtTime(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rem = (s - m * 60).toFixed(1).padStart(4, '0');
  return `${String(m).padStart(2, '0')}:${rem}`;
}

function fmtEta(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec) || sec <= 0) return '--:--';
  const total = Math.ceil(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function easeOutCubic(u: number): number {
  const c = Math.max(0, Math.min(1, u));
  return 1 - Math.pow(1 - c, 3);
}

function snapCurve(u: number): number {
  const c = Math.max(0, Math.min(1, u));
  if (c <= 0.18) {
    return 1 - Math.pow(1 - c / 0.18, 2);
  }
  return 1.0;
}

export default function App() {
  const [theme, setTheme] = useState<ThemePreset>('blue_night');
  const [projectName, setProjectName] = useState('وثائقي الحضارة الأندلسية');
  const [stage, setStage] = useState<Stage>('inputs');
  const [rawText, setRawText] = useState(INITIAL_SCRIPT);
  const [segments, setSegments] = useState<CutSegment[]>(INITIAL_SEGMENTS);
  const [motionTab, setMotionTab] = useState<MotionType>('zoom_in');
  const [globalStrength, setGlobalStrength] = useState(15);
  const [motionDuration, setMotionDuration] = useState(3.5);
  const [resourceMode, setResourceMode] = useState<ResourceMode>('light');
  const [assignments, setAssignments] = useState<Record<number, MotionAssignment>>({
    1: { type: 'zoom_in', x: 0.36, y: 0.42, strength: 15, ease: true, duration: 3.5 },
  });
  const [currentImageId, setCurrentImageId] = useState<number>(1);

  // Audio upload & Stage 1 sliced clips
  const [audioFileName, setAudioFileName] = useState('narration_andalus.wav');
  const [audioDuration, setAudioDuration] = useState(10.5);
  const [audioBuffer, setAudioBuffer] = useState<AudioBuffer | null>(null);
  const [slicedClips, setSlicedClips] = useState<Record<number, SlicedClip>>({});
  const [playingSegId, setPlayingSegId] = useState<number | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const audioInputRef = useRef<HTMLInputElement | null>(null);

  // Stage 2 per-scene render status
  const [renderedScenes, setRenderedScenes] = useState<Record<number, boolean>>({});

  // Two-stage chunked job state (with Pause / Resume & ETA)
  const [jobActive, setJobActive] = useState(false);
  const [jobStageLabel, setJobStageLabel] = useState('مرحلة 1/2');
  const [jobProgress, setJobProgress] = useState(0);
  const [jobStatus, setJobStatus] = useState('');
  const [jobEtaSec, setJobEtaSec] = useState<number | null>(null);
  const [jobPaused, setJobPaused] = useState(false);
  const pausedRef = useRef(false);
  const cancelledRef = useRef(false);

  // Instant Live Motion Preview state (Stage 4)
  const [livePreviewSegId, setLivePreviewSegId] = useState<number | null>(null);
  const [livePreviewElapsed, setLivePreviewElapsed] = useState(0);
  const [liveTransform, setLiveTransform] = useState('scale(1) translate3d(0%, 0%, 0)');
  const previewRafRef = useRef<number | null>(null);

  // Bottom thumbnail auto-scroll refs
  const thumbRefs = useRef<Record<number, HTMLButtonElement | null>>({});

  // Modals & PWA state
  const [showInstallModal, setShowInstallModal] = useState(false);
  const [showIOSGuide, setShowIOSGuide] = useState(false);
  const [savedLabel, setSavedLabel] = useState('تم الحفظ ✓');
  const idbHydratedRef = useRef(false);

  const { isInstallable, isInstalled, isIOS, install } = usePWAInstall();
  const isOnline = useOnlineStatus();

  // Hydrate workspace from IndexedDB on load
  useEffect(() => {
    let active = true;
    loadWorkspaceIDB<{
      theme?: ThemePreset;
      projectName?: string;
      rawText?: string;
      segments?: CutSegment[];
      assignments?: Record<number, MotionAssignment>;
      resourceMode?: ResourceMode;
      motionDuration?: number;
      globalStrength?: number;
      renderedScenes?: Record<number, boolean>;
    }>().then((saved) => {
      if (!active) return;
      if (saved) {
        if (saved.theme) {
          setTheme(saved.theme);
          document.documentElement.setAttribute('data-theme', saved.theme);
        }
        if (saved.projectName) setProjectName(saved.projectName);
        if (saved.rawText) setRawText(saved.rawText);
        if (Array.isArray(saved.segments) && saved.segments.length > 0) setSegments(saved.segments);
        if (saved.assignments) setAssignments(saved.assignments);
        if (saved.resourceMode) setResourceMode(saved.resourceMode);
        if (typeof saved.motionDuration === 'number') setMotionDuration(saved.motionDuration);
        if (typeof saved.globalStrength === 'number') setGlobalStrength(saved.globalStrength);
        if (saved.renderedScenes) setRenderedScenes(saved.renderedScenes);
      }
      idbHydratedRef.current = true;
    });
    return () => {
      active = false;
    };
  }, []);

  // Auto-save to IndexedDB whenever project workspace changes
  useEffect(() => {
    if (!idbHydratedRef.current) return;
    setSavedLabel('جاري الحفظ...');
    const timer = window.setTimeout(() => {
      saveWorkspaceIDB({
        theme,
        projectName,
        rawText,
        segments,
        assignments,
        resourceMode,
        motionDuration,
        globalStrength,
        renderedScenes,
      }).then(() => setSavedLabel('تم الحفظ محلياً ✓'));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [theme, projectName, rawText, segments, assignments, resourceMode, motionDuration, globalStrength, renderedScenes]);

  // Smoothly center the selected scene in the bottom timeline strip (free-order navigation)
  useEffect(() => {
    if (stage !== 'motion') return;
    const btn = thumbRefs.current[currentImageId];
    if (btn && typeof btn.scrollIntoView === 'function') {
      btn.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }
  }, [currentImageId, stage]);

  // Cleanup animation frame & audio playback on unmount
  useEffect(() => {
    return () => {
      if (previewRafRef.current) cancelAnimationFrame(previewRafRef.current);
      if (audioElRef.current) {
        audioElRef.current.pause();
        audioElRef.current = null;
      }
    };
  }, []);

  const parsedCount = useMemo(() => {
    return rawText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => /^\[?[0-9٠-٩]+/.test(l)).length;
  }, [rawText]);

  // All image scenes remain freely accessible in any order (non-blocking timeline)
  const imageSegments = useMemo(
    () => segments.filter((s) => s.sceneType === 'image'),
    [segments]
  );

  const activeImage = useMemo(() => {
    return imageSegments.find((x) => x.id === currentImageId) || imageSegments[0] || null;
  }, [imageSegments, currentImageId]);

  const activeIdx = activeImage
    ? imageSegments.findIndex((x) => x.id === activeImage.id)
    : -1;

  const stopLivePreview = () => {
    if (previewRafRef.current) {
      cancelAnimationFrame(previewRafRef.current);
      previewRafRef.current = null;
    }
    setLivePreviewSegId(null);
    setLivePreviewElapsed(0);
    setLiveTransform('scale(1) translate3d(0%, 0%, 0)');
  };

  const playInstantSceneMotion = (
    overrideType?: MotionType,
    overrideFocus?: { x: number; y: number }
  ) => {
    if (!activeImage) return;
    stopLivePreview();

    const assigned = assignments[activeImage.id];
    const mType: MotionType = overrideType ?? assigned?.type ?? motionTab;
    const fx = overrideFocus?.x ?? assigned?.x ?? 0.5;
    const fy = overrideFocus?.y ?? assigned?.y ?? 0.5;
    const strength = (assigned?.strength ?? globalStrength) / 100;
    const dur = Math.max(2.0, Math.min(6.0, assigned?.duration ?? motionDuration));
    const useEase = assigned?.ease ?? true;

    setLivePreviewSegId(activeImage.id);
    const startTs = performance.now();

    const step = (now: number) => {
      const elapsedSec = (now - startTs) / 1000;
      const u = Math.min(1, elapsedSec / dur);
      const t = mType === 'snap_zoom' ? snapCurve(u) : useEase ? easeOutCubic(u) : u;

      const maxScale = mType === 'snap_zoom' ? 1.24 + strength * 0.55 : 1 + strength * 0.72;
      let scale = 1;
      let panX = 0;
      let panY = 0;

      if (mType === 'zoom_in' || mType === 'snap_zoom') {
        scale = 1 + (maxScale - 1) * t;
        panX = (0.5 - fx) * (scale - 1) * 85;
        panY = (0.5 - fy) * (scale - 1) * 85;
      } else if (mType === 'zoom_out') {
        scale = maxScale - (maxScale - 1) * t;
        panX = (0.5 - fx) * (scale - 1) * 85;
        panY = (0.5 - fy) * (scale - 1) * 85;
      } else if (mType === 'left') {
        scale = 1 + Math.max(0.14, strength * 0.65);
        panX = (1 - 2 * t) * 7.5;
        panY = (0.5 - fy) * 8;
      } else if (mType === 'right') {
        scale = 1 + Math.max(0.14, strength * 0.65);
        panX = (-1 + 2 * t) * 7.5;
        panY = (0.5 - fy) * 8;
      }

      setLivePreviewElapsed(Math.min(dur, elapsedSec));
      setLiveTransform(
        `translate3d(${panX.toFixed(2)}%, ${panY.toFixed(2)}%, 0) scale(${scale.toFixed(4)})`
      );

      if (u < 1) {
        previewRafRef.current = requestAnimationFrame(step);
      } else {
        previewRafRef.current = null;
        window.setTimeout(() => {
          setLivePreviewSegId((curr) => (curr === activeImage.id ? null : curr));
          setLiveTransform('scale(1) translate3d(0%, 0%, 0)');
        }, 420);
      }
    };

    previewRafRef.current = requestAnimationFrame(step);
  };

  const handleStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!activeImage) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Number(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)).toFixed(3));
    const y = Number(Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)).toFixed(3));
    setAssignments((prev) => ({
      ...prev,
      [activeImage.id]: {
        type: motionTab,
        x,
        y,
        strength: prev[activeImage.id]?.strength ?? globalStrength,
        ease: prev[activeImage.id]?.ease ?? true,
        duration: prev[activeImage.id]?.duration ?? motionDuration,
      },
    }));
  };

  const applySnapZoomNow = () => {
    if (!activeImage) return;
    const curr = assignments[activeImage.id];
    const x = curr?.x ?? 0.5;
    const y = curr?.y ?? 0.5;
    setMotionTab('snap_zoom');
    setAssignments((prev) => ({
      ...prev,
      [activeImage.id]: {
        type: 'snap_zoom',
        x,
        y,
        strength: curr?.strength ?? globalStrength,
        ease: true,
        duration: curr?.duration ?? motionDuration,
      },
    }));
    playInstantSceneMotion('snap_zoom', { x, y });
  };

  const handleSelectSceneFreeOrder = (seg: CutSegment) => {
    stopLivePreview();
    setCurrentImageId(seg.id);
    const existing = assignments[seg.id];
    if (existing) {
      setMotionTab(existing.type);
    }
  };

  const nudgeStart = (id: number, delta: number) => {
    setSegments((prev) => {
      const idx = prev.findIndex((s) => s.id === id);
      if (idx <= 0) return prev;
      const copy = prev.map((s) => ({ ...s }));
      const minT = copy[idx - 1].start + 0.2;
      const maxT = copy[idx].end - 0.2;
      const nextStart =
        Math.round(Math.max(minT, Math.min(maxT, copy[idx].start + delta)) * 10) / 10;
      copy[idx].start = nextStart;
      copy[idx - 1].end = nextStart;
      return copy;
    });
  };

  const handleAudioUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setAudioFileName(file.name);
    try {
      const { buffer, duration } = await decodeAudioFileToBuffer(file);
      setAudioBuffer(buffer);
      setAudioDuration(Number(duration.toFixed(1)));
      // Rescale demo segments proportionally to real uploaded audio if needed
      setSegments((prev) => {
        const count = prev.length || 1;
        const step = duration / count;
        return prev.map((s, i) => ({
          ...s,
          start: Number((i * step).toFixed(2)),
          end: Number(((i + 1) * step).toFixed(2)),
        }));
      });
      setSlicedClips({});
    } catch {
      setAudioBuffer(null);
    }
  };

  // Helper to wait for cooldown & pause/cancel flags (single-worker non-throttling browser pacing)
  const waitWithPause = async (ms: number): Promise<boolean> => {
    const startWait = performance.now();
    while (performance.now() - startWait < ms || pausedRef.current) {
      if (cancelledRef.current) return false;
      await new Promise((r) => setTimeout(r, 90));
    }
    return !cancelledRef.current;
  };

  // Stage 1: Immediate Audio Slicing (قص الصوت يبدأ فورًا)
  const runStage1AudioSlice = async (targetStageOnFinish?: Stage): Promise<boolean> => {
    if (jobActive) return false;
    const modeMeta = RESOURCE_MODES.find((m) => m.id === resourceMode) || RESOURCE_MODES[0];

    pausedRef.current = false;
    cancelledRef.current = false;
    setJobPaused(false);
    setJobActive(true);
    setJobStageLabel('مرحلة 1/2 · قص الصوت');
    setJobProgress(4);
    setJobStatus('بدء قص الصوت فوراً إلى مقاطع مستقلة...');
    setJobEtaSec((segments.length * modeMeta.cooldownMs) / 1000);

    const startedAt = performance.now();
    for (let i = 0; i < segments.length; i++) {
      if (!(await waitWithPause(modeMeta.cooldownMs))) {
        setJobActive(false);
        return false;
      }
      const seg = segments[i];
      const wavBlob = encodeAudioBufferSliceToWav(audioBuffer, seg.start, seg.end, seg.id);
      const url = URL.createObjectURL(wavBlob);
      setSlicedClips((prev) => {
        const old = prev[seg.id];
        if (old?.url) URL.revokeObjectURL(old.url);
        return {
          ...prev,
          [seg.id]: {
            segId: seg.id,
            url,
            duration: Number((seg.end - seg.start).toFixed(2)),
            start: seg.start,
            end: seg.end,
            sizeBytes: wavBlob.size,
          },
        };
      });

      const done = i + 1;
      const pct = Math.round((done / segments.length) * 100);
      const elapsedSec = (performance.now() - startedAt) / 1000;
      const avgPerItem = elapsedSec / done;
      setJobProgress(pct);
      setJobEtaSec(Math.max(0, (segments.length - done) * avgPerItem));
      setJobStatus(`قص مقطع صوتي ${done}/${segments.length} (${seg.sceneFile})...`);
    }

    setJobProgress(100);
    setJobEtaSec(0);
    setJobStatus('اكتمل قص الصوت فوراً ✓ جميع المقاطع جاهزة للتشغيل');
    await waitWithPause(280);
    setJobActive(false);
    if (targetStageOnFinish) setStage(targetStageOnFinish);
    return true;
  };

  // Stage 2: Sequential Single-Worker Per-Scene Rendering (or Full 2-Stage Pipeline)
  const runTwoStageExport = async (includeSlice = true) => {
    if (jobActive) return;
    if (includeSlice && Object.keys(slicedClips).length < segments.length) {
      const ok = await runStage1AudioSlice();
      if (!ok) return;
    }

    const modeMeta = RESOURCE_MODES.find((m) => m.id === resourceMode) || RESOURCE_MODES[0];
    pausedRef.current = false;
    cancelledRef.current = false;
    setJobPaused(false);
    setJobActive(true);
    setJobStageLabel('مرحلة 2/2 · معالجة المشاهد');
    setJobProgress(5);
    setJobStatus(`تجهيز المشاهد بنمط «${modeMeta.label}» (عامل واحد لمنع السخونة)...`);

    const startedAt = performance.now();
    for (let i = 0; i < segments.length; i++) {
      if (!(await waitWithPause(modeMeta.cooldownMs + 140))) {
        setJobActive(false);
        return;
      }
      const seg = segments[i];
      setRenderedScenes((prev) => ({ ...prev, [seg.id]: true }));
      const done = i + 1;
      const elapsedSec = (performance.now() - startedAt) / 1000;
      setJobProgress(Math.round((done / segments.length) * 100));
      setJobEtaSec(Math.max(0, (segments.length - done) * (elapsedSec / done)));
      setJobStatus(`تم تجهيز مشهد #${seg.id} (${done}/${segments.length}) بنجاح`);
    }

    setJobProgress(100);
    setJobEtaSec(0);
    setJobStatus('اكتملت المرحلة 2/2 بنجاح ✓ كل مشهد جاهز ومستقل');
    await waitWithPause(300);
    setJobActive(false);
    setStage('export');
  };

  const renderSingleScene = async (segId: number) => {
    setRenderedScenes((prev) => ({ ...prev, [segId]: false }));
    await new Promise((r) => setTimeout(r, 260));
    setRenderedScenes((prev) => ({ ...prev, [segId]: true }));
  };

  const togglePlaySlice = (clip: SlicedClip) => {
    if (playingSegId === clip.segId && audioElRef.current) {
      audioElRef.current.pause();
      setPlayingSegId(null);
      return;
    }
    if (audioElRef.current) {
      audioElRef.current.pause();
    }
    const audio = new Audio(clip.url);
    audioElRef.current = audio;
    setPlayingSegId(clip.segId);
    audio.onended = () => setPlayingSegId((curr) => (curr === clip.segId ? null : curr));
    audio.play().catch(() => setPlayingSegId(null));
  };

  const togglePauseJob = () => {
    const next = !pausedRef.current;
    pausedRef.current = next;
    setJobPaused(next);
  };

  const cancelJob = () => {
    cancelledRef.current = true;
    pausedRef.current = false;
    setJobPaused(false);
    setJobActive(false);
  };

  const selectTheme = (next: ThemePreset) => {
    setTheme(next);
    document.documentElement.setAttribute('data-theme', next);
  };

  const stages: { id: Stage; num: number; label: string }[] = [
    { id: 'inputs', num: 1, label: 'المدخلات' },
    { id: 'cuts', num: 2, label: 'التفريغ وقص الصوت' },
    { id: 'scenes', num: 3, label: 'المشاهد' },
    { id: 'motion', num: 4, label: 'الحركات' },
    { id: 'export', num: 5, label: 'التصدير' },
  ];

  const slicedCount = Object.keys(slicedClips).length;
  const renderedCount = segments.filter((s) => renderedScenes[s.id]).length;

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ background: 'var(--bg-app)', color: 'var(--text-primary)' }}
    >
      {/* Top Bar */}
      <header
        className="sticky top-0 z-30 min-h-15 px-4 sm:px-6 py-2 flex flex-wrap items-center justify-between gap-3 border-b"
        style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
      >
        <div className="flex items-center gap-2.5 whitespace-nowrap">
          <span
            className="w-8 h-8 rounded-lg flex items-center justify-center border"
            style={{
              background: 'rgba(108,155,255,0.14)',
              borderColor: 'var(--border)',
              color: 'var(--accent)',
            }}
          >
            <Film className="w-4 h-4" />
          </span>
          <span className="text-lg font-bold">أوتوكَت · AutoCut</span>
          <span
            className="text-xs font-mono px-2 py-0.5 rounded border"
            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            v1.3.0
          </span>
        </div>

        <div className="hidden md:flex items-center gap-3 text-sm">
          <span className="font-semibold">{projectName}</span>
          <span className="text-xs font-semibold" style={{ color: 'var(--success-text)' }}>
            {savedLabel}
          </span>
          {!isOnline && (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold border"
              style={{ borderColor: 'var(--warning-text)', color: 'var(--warning-text)' }}
            >
              <WifiOff className="w-3 h-3" />
              أوفلاين
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {!isInstalled && isInstallable && (
            <button
              onClick={install}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
              style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
            >
              <Smartphone className="w-3.5 h-3.5" />
              تثبيت التطبيق
            </button>
          )}
          {!isInstalled && !isInstallable && isIOS && (
            <button
              onClick={() => setShowIOSGuide(true)}
              className="px-3 py-1.5 rounded-lg border text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
              style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
            >
              <Smartphone className="w-3.5 h-3.5" />
              تثبيت على الآيفون
            </button>
          )}

          <div
            className="inline-flex items-center p-0.5 rounded-lg border gap-0.5"
            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
            role="group"
            aria-label="المظهر"
          >
            {THEME_OPTIONS.map((t) => {
              const active = theme === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => selectTheme(t.id)}
                  className="px-2.5 py-1 rounded-md text-xs font-semibold cursor-pointer transition-colors"
                  style={{
                    background: active ? 'var(--accent-btn)' : 'transparent',
                    color: active ? 'var(--on-accent)' : 'var(--text-muted)',
                  }}
                >
                  {t.label}
                </button>
              );
            })}
          </div>
          <div
            className="hidden sm:flex px-3 py-1.5 rounded-lg text-xs font-medium border items-center gap-1.5"
            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            <Sliders className="w-3.5 h-3.5" />
            1920×1080 · 30fps
          </div>
        </div>
      </header>

      {/* Stepper */}
      <nav
        className="px-6 py-2.5 border-b overflow-x-auto"
        style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
      >
        <div className="max-w-7xl mx-auto flex items-center gap-2">
          {stages.map((st, idx) => {
            const active = stage === st.id;
            const done = stages.findIndex((x) => x.id === stage) > idx;
            return (
              <React.Fragment key={st.id}>
                <button
                  onClick={() => setStage(st.id)}
                  className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm font-medium border transition-colors cursor-pointer whitespace-nowrap"
                  style={{
                    background: active ? 'rgba(108,155,255,0.14)' : 'transparent',
                    borderColor: active ? 'var(--border)' : 'transparent',
                    color: active ? 'var(--accent)' : done ? 'var(--text-primary)' : 'var(--text-muted)',
                  }}
                >
                  <span
                    className="w-5 h-5 rounded text-xs font-bold inline-flex items-center justify-center tabular-nums"
                    style={{
                      background: active ? 'var(--accent-btn)' : done ? 'rgba(87,211,155,0.16)' : 'var(--bg-raised)',
                      color: active ? 'var(--on-accent)' : done ? 'var(--success-text)' : 'var(--text-muted)',
                    }}
                  >
                    {done ? '✓' : st.num}
                  </span>
                  {st.label}
                </button>
                {idx < stages.length - 1 && (
                  <span style={{ color: 'var(--border)' }}>←</span>
                )}
              </React.Fragment>
            );
          })}
        </div>
      </nav>

      {/* Main Content */}
      <main className="flex-1 w-full max-w-7xl mx-auto p-6">
        {/* Windows Local Package Banner + Download ZIP Button */}
        <div
          className="mb-5 px-4 py-3.5 rounded-xl border flex flex-wrap items-center justify-between gap-3 text-xs"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        >
          <div className="flex items-center gap-2.5">
            <Terminal className="w-4 h-4 shrink-0" style={{ color: 'var(--accent)' }} />
            <div>
              <div className="font-semibold text-sm">
                تشغيل AutoCut v1.3.0 محلياً على جهازك (Windows 10/11 + تطبيق ويب PWA — مجاني 100%)
              </div>
              <div className="mt-0.5" style={{ color: 'var(--text-muted)' }}>
                تفريغ خارجي <span dir="ltr" className="font-mono">Vibe</span> (<span dir="ltr" className="font-mono">JSON / SRT / VTT</span>) + قص الصوت فوراً + تصدير هادئ بنمط «خفيف» للمشاريع الطويلة (45–60 دقيقة).
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setShowInstallModal(true)}
              className="px-3 py-2 rounded-lg border text-xs font-semibold cursor-pointer"
              style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
            >
              خطوات التثبيت والربط
            </button>
            <a
              href="/autocut-windows.zip"
              download="autocut-windows.zip"
              className="px-4 py-2 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5"
              style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
            >
              <Download className="w-3.5 h-3.5" />
              تنزيل حزمة AutoCut (.zip)
            </a>
          </div>
        </div>

        {/* Two-Stage Live Pipeline Runner with Pause / Resume & ETA */}
        {jobActive && (
          <div
            className="mb-6 p-4 rounded-xl border space-y-2.5"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm font-semibold">
              <div className="flex items-center gap-2.5 flex-wrap">
                <span
                  className="px-2.5 py-0.5 rounded-md text-xs font-bold border"
                  style={{
                    background: 'rgba(108,155,255,0.14)',
                    borderColor: 'var(--accent)',
                    color: 'var(--accent)',
                  }}
                >
                  {jobStageLabel}
                </span>
                <span>{jobPaused ? 'متوقف مؤقتاً — يمكنك الاستئناف في أي وقت' : jobStatus}</span>
              </div>
              <div className="flex items-center gap-2.5 text-xs">
                <span className="font-mono tabular-nums" style={{ color: 'var(--text-muted)' }}>
                  المتبقي: {fmtEta(jobEtaSec)}
                </span>
                <span className="font-mono tabular-nums">{jobProgress}%</span>
                <button
                  onClick={togglePauseJob}
                  className="px-2.5 py-1 rounded-lg border text-xs font-semibold inline-flex items-center gap-1 cursor-pointer"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                >
                  {jobPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                  {jobPaused ? 'استئناف' : 'إيقاف مؤقت'}
                </button>
                <button
                  onClick={cancelJob}
                  className="px-2.5 py-1 rounded-lg border text-xs font-semibold cursor-pointer"
                  style={{ borderColor: 'var(--danger-text)', color: 'var(--danger-text)' }}
                >
                  إلغاء
                </button>
              </div>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--bg-raised)' }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${jobProgress}%`, background: 'var(--accent)' }}
              />
            </div>
          </div>
        )}

        {/* Stage 1: New Project 2x2 Grid */}
        {stage === 'inputs' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">مشروع جديد</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  رتّب المواد الأربع هنا، أو ارفع ملف الصوت الحقيقي لقص المقاطع فوراً بنمط تشغيل هادئ.
                </p>
              </div>
              <button
                onClick={() => setStage('cuts')}
                className="px-4 py-2 rounded-xl border text-xs font-semibold cursor-pointer"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                فتح شاشة التفريغ وقص الصوت
              </button>
            </div>

            {/* Top Row: Project Name + Resource Mode Control */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div
                className="p-4 rounded-xl border"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <label className="block text-xs font-semibold mb-1.5">اسم المشروع</label>
                <input
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border text-sm"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                />
              </div>

              <div
                className="p-4 rounded-xl border flex flex-col justify-between gap-2"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-xs font-bold">
                    <Cpu className="w-4 h-4" style={{ color: 'var(--accent)' }} />
                    وضع استهلاك الموارد (للفيديوهات 45–60 دقيقة)
                  </div>
                  <div
                    className="inline-flex p-0.5 rounded-lg border gap-0.5"
                    style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                  >
                    {RESOURCE_MODES.map((m) => {
                      const active = resourceMode === m.id;
                      return (
                        <button
                          key={m.id}
                          onClick={() => setResourceMode(m.id)}
                          className="px-2.5 py-1 rounded-md text-xs font-semibold cursor-pointer transition-colors"
                          style={{
                            background: active ? 'var(--accent-btn)' : 'transparent',
                            color: active ? 'var(--on-accent)' : 'var(--text-muted)',
                          }}
                        >
                          {m.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {RESOURCE_MODES.find((m) => m.id === resourceMode)?.hint}
                </div>
              </div>
            </div>

            {/* 2x2 Materials Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* 1. Audio (with real local file upload for WAV slicing) */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>1</span>
                    الصوت
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded border" style={{ color: 'var(--success-text)', borderColor: 'var(--success-text)' }}>
                    جاهز ✓
                  </span>
                </div>
                <div
                  className="p-3.5 rounded-xl border border-dashed flex flex-wrap items-center justify-between gap-2 text-xs"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                >
                  <div>
                    <div className="font-semibold text-sm">{audioFileName}</div>
                    <div className="mt-1 tabular-nums" style={{ color: 'var(--text-muted)' }}>
                      المدة: {audioDuration} ث · قص الصوت الفوري لا يغيّر جودة الصوت الأصلي
                    </div>
                  </div>
                  <input
                    ref={audioInputRef}
                    type="file"
                    accept="audio/*"
                    onChange={handleAudioUpload}
                    className="hidden"
                  />
                  <button
                    onClick={() => audioInputRef.current?.click()}
                    className="px-3 py-1.5 rounded-lg border text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
                    style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                  >
                    <Upload className="w-3.5 h-3.5" />
                    اختيار ملف صوت
                  </button>
                </div>
              </section>

              {/* 2. External Transcript */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>2</span>
                    التفريغ
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded border" style={{ color: 'var(--success-text)', borderColor: 'var(--success-text)' }}>
                    JSON — توقيت كل كلمة ✓
                  </span>
                </div>
                <div
                  className="p-3 rounded-lg border text-xs leading-5"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                >
                  <strong style={{ color: 'var(--text-primary)' }}>دليل التصدير من Vibe:</strong> افتح <span dir="ltr" className="font-mono">More Options</span> ← فعّل <span dir="ltr" className="font-mono">Word timestamps</span> ← صدّر بصيغة <span dir="ltr" className="font-mono">JSON</span> (يقبل أيضاً <span dir="ltr" className="font-mono">SRT / VTT</span>).
                </div>
                <div className="flex items-center justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
                  <span dir="ltr" className="font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>narration_andalus.json</span>
                  <span className="tabular-nums">28 كلمة · التغطية 98%</span>
                </div>
              </section>

              {/* 3. Numbered Script */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>3</span>
                    النص المرقّم
                  </div>
                  <span className="text-xs font-semibold tabular-nums" style={{ color: 'var(--accent)' }}>
                    {parsedCount} مقاطع
                  </span>
                </div>
                <textarea
                  value={rawText}
                  onChange={(e) => setRawText(e.target.value)}
                  rows={5}
                  className="w-full p-3 rounded-xl border text-sm leading-7"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                />
                <div className="flex items-center justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
                  <span className="inline-flex items-center gap-1"><FileText className="w-3.5 h-3.5" /> يدعم اللصق أو استيراد ملف <span dir="ltr" className="font-mono">.txt</span></span>
                  <span>1..N</span>
                </div>
              </section>

              {/* 4. Scenes */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>4</span>
                    المشاهد
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded border" style={{ color: 'var(--success-text)', borderColor: 'var(--success-text)' }}>
                    5 مشاهد جاهزة ✓
                  </span>
                </div>
                <div className="flex gap-2">
                  <input
                    readOnly
                    dir="ltr"
                    value="C:\Projects\Andalus\scenes"
                    className="flex-1 px-3 py-2 rounded-lg border text-xs font-mono"
                    style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                  />
                  <span
                    className="px-3 py-2 rounded-lg border text-xs flex items-center gap-1.5"
                    style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                  >
                    <FolderOpen className="w-3.5 h-3.5" />
                    4 صور · 1 فيديو
                  </span>
                </div>
              </section>
            </div>

            {/* Settings Summary + Footer CTA */}
            <div
              className="mt-4 px-4 py-3 rounded-xl border flex flex-wrap items-center justify-between gap-3 text-xs"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
            >
              <span>
                الإعدادات الحالية:{' '}
                <strong style={{ color: 'var(--text-primary)' }}>
                  1920×1080 · 30 fps · مدة الحركة {motionDuration.toFixed(1)}ث · وضع الموارد: {RESOURCE_MODES.find((m) => m.id === resourceMode)?.label}
                </strong>
              </span>
              <span>
                مظهر:{' '}
                <strong style={{ color: 'var(--text-primary)' }}>
                  {THEME_OPTIONS.find((x) => x.id === theme)?.label}
                </strong>
              </span>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3 flex-wrap">
                <button
                  disabled={jobActive}
                  onClick={() => runStage1AudioSlice('cuts')}
                  className="px-6 py-2.5 rounded-xl font-semibold inline-flex items-center gap-2 cursor-pointer disabled:opacity-50"
                  style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
                >
                  <Scissors className="w-4 h-4" />
                  بدء المرحلة 1: قص الصوت فوراً
                </button>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  قص الصوت يبدأ فور المطابقة دون انتظار الرندر
                </span>
              </div>
              <button
                onClick={() => setStage('motion')}
                className="px-4 py-2 rounded-xl border text-xs font-semibold cursor-pointer"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                الانتقال الحر إلى حركات الصور
              </button>
            </div>
          </div>
        )}

        {/* Stage 2: Transcript Import, Cuts & Immediate Audio Slicing (CapCut Track) */}
        {stage === 'cuts' && (
          <div className="space-y-5">
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">2. التفريغ وقص الصوت الفوري (المرحلة 1/2)</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  قص مقاطع الصوت يبدأ فوراً ومستقل تماماً عن رندر الفيديو، مع شريط مقاطع أفقي للتشغيل والمعاينة.
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  disabled={jobActive}
                  onClick={() => runStage1AudioSlice()}
                  className="px-4 py-2.5 rounded-xl border text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--accent)', color: 'var(--accent)' }}
                >
                  <Scissors className="w-3.5 h-3.5" />
                  {slicedCount === segments.length ? 'إعادة قص المقاطع' : 'قص الصوت الآن'}
                </button>
                <button
                  onClick={() => setStage('scenes')}
                  className="px-5 py-2.5 rounded-xl font-semibold cursor-pointer"
                  style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
                >
                  حفظ ومتابعة
                </button>
              </div>
            </div>

            {/* CapCut-style Horizontal Audio Track Card */}
            <div
              className="p-4 rounded-2xl border space-y-3"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2 font-bold text-sm">
                  <Scissors className="w-4 h-4" style={{ color: 'var(--accent)' }} />
                  شريط المقاطع الصوتية المقصوصة ({slicedCount}/{segments.length})
                </div>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  اسحب أفقياً واضغط تشغيل لسماع كل مقطع مستقل بصيغة WAV
                </span>
              </div>

              <div className="flex gap-2.5 overflow-x-auto pb-2 smooth-scroll-x">
                {segments.map((s) => {
                  const clip = slicedClips[s.id];
                  const isPlaying = playingSegId === s.id;
                  const dur = s.end - s.start;
                  return (
                    <div
                      key={s.id}
                      className="min-w-[205px] shrink-0 p-3 rounded-xl border flex flex-col justify-between gap-2"
                      style={{
                        background: isPlaying ? 'rgba(108,155,255,0.14)' : 'var(--bg-raised)',
                        borderColor: isPlaying ? 'var(--accent)' : 'var(--border)',
                      }}
                    >
                      <div className="flex items-center justify-between text-xs font-bold">
                        <span>مقطع {s.id}</span>
                        <span className="font-mono tabular-nums" style={{ color: 'var(--text-muted)' }}>
                          {dur.toFixed(2)}s
                        </span>
                      </div>
                      <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                        {s.text}
                      </div>
                      <div className="flex items-center justify-between gap-2 pt-1">
                        {clip ? (
                          <>
                            <button
                              onClick={() => togglePlaySlice(clip)}
                              className="px-2.5 py-1 rounded-lg text-xs font-semibold inline-flex items-center gap-1 cursor-pointer"
                              style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
                            >
                              {isPlaying ? <Square className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                              {isPlaying ? 'إيقاف' : 'تشغيل'}
                            </button>
                            <a
                              href={clip.url}
                              download={`clip_${String(s.id).padStart(3, '0')}.wav`}
                              className="px-2 py-1 rounded-lg border text-[11px] font-semibold inline-flex items-center gap-1"
                              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                              title="تنزيل المقطع الصوتي"
                            >
                              <Download className="w-3 h-3" />
                              WAV
                            </a>
                          </>
                        ) : (
                          <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                            بانتظار القص الفوري
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Cuts Table */}
            <div
              className="rounded-2xl border overflow-x-auto"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <table className="w-full text-sm text-right border-collapse">
                <thead>
                  <tr style={{ background: 'var(--bg-raised)', color: 'var(--text-muted)' }}>
                    <th className="p-3">#</th>
                    <th className="p-3">النص المدخل</th>
                    <th className="p-3">النص المطابق</th>
                    <th className="p-3">البداية</th>
                    <th className="p-3">النهاية</th>
                    <th className="p-3">المدة</th>
                    <th className="p-3">الدقة</th>
                  </tr>
                </thead>
                <tbody>
                  {segments.map((s, idx) => (
                    <tr
                      key={s.id}
                      className="border-t"
                      style={{
                        borderColor: 'var(--border)',
                        background: s.confidence < 70 ? 'rgba(244,184,96,0.08)' : 'transparent',
                      }}
                    >
                      <td className="p-3 font-bold tabular-nums">{s.id}</td>
                      <td className="p-3">{s.text}</td>
                      <td className="p-3" style={{ color: 'var(--text-muted)' }}>{s.matchedText}</td>
                      <td className="p-3">
                        <div className="inline-flex items-center gap-1" dir="ltr">
                          <button
                            disabled={idx === 0}
                            onClick={() => nudgeStart(s.id, -0.1)}
                            className="w-7 h-7 rounded border text-xs cursor-pointer disabled:opacity-40"
                            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                          >
                            -
                          </button>
                          <span className="w-14 text-center font-mono text-xs tabular-nums">
                            {s.start.toFixed(2)}
                          </span>
                          <button
                            disabled={idx === 0}
                            onClick={() => nudgeStart(s.id, 0.1)}
                            className="w-7 h-7 rounded border text-xs cursor-pointer disabled:opacity-40"
                            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                          >
                            +
                          </button>
                        </div>
                      </td>
                      <td className="p-3 font-mono text-xs tabular-nums">{fmtTime(s.end)}</td>
                      <td className="p-3 font-mono text-xs tabular-nums">{(s.end - s.start).toFixed(2)}s</td>
                      <td className="p-3">
                        <span
                          className="px-2.5 py-0.5 rounded-md text-xs font-semibold tabular-nums border"
                          style={{
                            borderColor: s.confidence >= 80 ? 'var(--success-text)' : 'var(--warning-text)',
                            color: s.confidence >= 80 ? 'var(--success-text)' : 'var(--warning-text)',
                          }}
                        >
                          {s.confidence}%
                        </span>
                        {s.estimated && (
                          <span
                            className="mr-1.5 px-2 py-0.5 rounded text-[11px] font-bold border"
                            style={{ borderColor: 'var(--warning-text)', color: 'var(--warning-text)' }}
                          >
                            تقدير
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Stage 3: Scenes */}
        {stage === 'scenes' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-5 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">3. توزيع المشاهد على الخط الزمني</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  خط زمني متصل بدون أي فراغات؛ الفيديوهات صامتة وتمتد لتطابق مدة المقطع بالكامل.
                </p>
              </div>
              <button
                onClick={() => setStage('motion')}
                className="px-5 py-2.5 rounded-xl font-semibold cursor-pointer"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                متابعة إلى الحركات
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
              {segments.map((s) => {
                const segDur = s.end - s.start;
                const speed = s.videoDuration ? s.videoDuration / segDur : 1;
                return (
                  <div
                    key={s.id}
                    className="rounded-xl border overflow-hidden"
                    style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                  >
                    <div
                      className="h-28 relative flex items-center justify-center text-white font-bold text-2xl tabular-nums"
                      style={{ background: s.color }}
                    >
                      #{s.id}
                    </div>
                    <div className="p-3 text-xs space-y-1">
                      <div className="flex justify-between">
                        <span>{s.sceneType === 'video' ? `فيديو · ${speed.toFixed(2)}x` : 'صورة'}</span>
                        <span className="font-mono tabular-nums">{segDur.toFixed(2)}s</span>
                      </div>
                      <div dir="ltr" className="truncate" style={{ color: 'var(--text-muted)' }}>
                        {s.sceneFile}
                      </div>
                      {s.sceneType === 'video' && speed < 0.4 && (
                        <div className="inline-block px-2 py-0.5 rounded font-semibold" style={{ color: 'var(--warning-text)' }}>
                          سرعة بطيئة جداً: {speed.toFixed(2)}x
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Stage 4: Free-Order Motion Studio + Instant Preview + Snap Zoom */}
        {stage === 'motion' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">4. حركات الصور (تنقّل حر ومعاينة فورية)</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  انتقل لأي مشهد بأي ترتيب دون قيود، ضع النقطة الزرقاء (<span dir="ltr" className="font-mono">#4C8DFF</span>)، وشغّل الحركة فوراً.
                </p>
              </div>
              <button
                onClick={() => {
                  stopLivePreview();
                  setStage('export');
                }}
                className="px-6 py-2.5 rounded-xl font-semibold cursor-pointer"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                تنفيذ المرحلة 2 والتصدير
              </button>
            </div>

            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <div
                className="inline-flex flex-wrap p-1 rounded-xl border gap-1"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                {MOTION_TABS.map((t) => {
                  const active = motionTab === t.id;
                  const count = imageSegments.filter((i) => assignments[i.id]?.type === t.id).length;
                  return (
                    <button
                      key={t.id}
                      onClick={() => setMotionTab(t.id)}
                      className="px-3.5 py-1.5 rounded-lg text-xs font-semibold cursor-pointer flex items-center gap-1.5"
                      style={{
                        background: active ? 'var(--accent-btn)' : 'transparent',
                        color: active ? 'var(--on-accent)' : 'var(--text-muted)',
                      }}
                    >
                      <span>{t.icon}</span>
                      <span>{t.label}</span>
                      <span className="tabular-nums opacity-85">{count}</span>
                    </button>
                  );
                })}
              </div>

              <div className="flex items-center gap-4 flex-wrap text-xs">
                <label className="flex items-center gap-2">
                  <span>القوة:</span>
                  <input
                    type="range"
                    min={5}
                    max={40}
                    value={globalStrength}
                    onChange={(e) => setGlobalStrength(Number(e.target.value))}
                  />
                  <span className="font-mono tabular-nums">{globalStrength}%</span>
                </label>

                <label className="flex items-center gap-2">
                  <span>مدة الحركة:</span>
                  <input
                    type="range"
                    min={2}
                    max={6}
                    step={0.5}
                    value={motionDuration}
                    onChange={(e) => setMotionDuration(Number(e.target.value))}
                  />
                  <span className="font-mono tabular-nums">{motionDuration.toFixed(1)}ث</span>
                </label>
              </div>
            </div>

            {activeImage && (
              <div className="grid grid-cols-[44px_1fr_44px] items-center gap-3 mb-4">
                <button
                  disabled={activeIdx <= 0}
                  onClick={() =>
                    activeIdx > 0 && handleSelectSceneFreeOrder(imageSegments[activeIdx - 1])
                  }
                  className="w-11 h-11 rounded-full border flex items-center justify-center cursor-pointer disabled:opacity-35"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <ChevronRight className="w-5 h-5" />
                </button>

                <div
                  className="rounded-2xl border p-4 flex flex-col items-center justify-center"
                  style={{ background: '#08090C', borderColor: 'var(--border)' }}
                >
                  <div className="w-full flex flex-wrap items-center justify-between gap-2 text-xs mb-3 text-slate-300">
                    <span className="tabular-nums font-semibold">
                      صورة {activeIdx + 1} من {imageSegments.length} · مشهد #{activeImage.id}
                    </span>
                    <div className="flex items-center gap-2 flex-wrap">
                      {livePreviewSegId === activeImage.id ? (
                        <button
                          onClick={stopLivePreview}
                          className="px-3 py-1 rounded-lg bg-slate-700 text-white font-semibold flex items-center gap-1.5 cursor-pointer"
                        >
                          <Square className="w-3 h-3" />
                          إيقاف العرض ({livePreviewElapsed.toFixed(1)}ث)
                        </button>
                      ) : (
                        <button
                          onClick={() => playInstantSceneMotion()}
                          className="px-3 py-1 rounded-lg font-semibold flex items-center gap-1.5 cursor-pointer"
                          style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
                        >
                          <Play className="w-3 h-3" />
                          تحريك المشهد
                        </button>
                      )}

                      <button
                        onClick={applySnapZoomNow}
                        className="px-3 py-1 rounded-lg border font-semibold flex items-center gap-1 cursor-pointer"
                        style={{
                          borderColor: 'var(--accent)',
                          color: '#E8EDF3',
                          background: 'rgba(108,155,255,0.18)',
                        }}
                      >
                        <Zap className="w-3 h-3" />
                        حركة خاطفة
                      </button>

                      {assignments[activeImage.id] && (
                        <button
                          onClick={() => {
                            stopLivePreview();
                            setAssignments((prev) => {
                              const next = { ...prev };
                              delete next[activeImage.id];
                              return next;
                            });
                          }}
                          className="px-2.5 py-1 rounded bg-red-500/20 text-red-300 flex items-center gap-1 cursor-pointer"
                        >
                          <Trash2 className="w-3 h-3" />
                          مسح النقطة
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Stage Preview Viewport with live requestAnimationFrame Zoom/Pan */}
                  <div
                    onClick={handleStageClick}
                    className="relative w-full max-w-xl aspect-video rounded-lg cursor-crosshair overflow-hidden select-none"
                  >
                    <div
                      className="w-full h-full flex flex-col items-center justify-center text-white/95 text-xl font-bold will-change-transform"
                      style={{
                        background: activeImage.color,
                        transform: livePreviewSegId === activeImage.id ? liveTransform : 'scale(1)',
                      }}
                    >
                      <span>مشهد #{activeImage.id} — {activeImage.sceneFile}</span>
                      <span className="text-xs font-normal text-white/70 mt-1">
                        {assignments[activeImage.id]
                          ? `الحركة: ${
                              MOTION_TABS.find((t) => t.id === assignments[activeImage.id].type)?.label
                            }`
                          : 'انقر في أي نقطة لاختيار موضع التركيز'}
                      </span>
                    </div>

                    {livePreviewSegId === activeImage.id && (
                      <div className="absolute top-2.5 right-2.5 px-2.5 py-1 rounded-md bg-black/70 text-white text-[11px] font-mono tabular-nums pointer-events-none">
                        ▶ عرض مباشر · {livePreviewElapsed.toFixed(1)} / {motionDuration.toFixed(1)}ث
                      </div>
                    )}

                    {assignments[activeImage.id] && (
                      <div
                        className="absolute w-6 h-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] border-white focus-dot-pulse pointer-events-none"
                        style={{
                          background: '#4C8DFF',
                          left: `${assignments[activeImage.id].x * 100}%`,
                          top: `${assignments[activeImage.id].y * 100}%`,
                        }}
                      />
                    )}
                  </div>
                </div>

                <button
                  disabled={activeIdx >= imageSegments.length - 1}
                  onClick={() =>
                    activeIdx < imageSegments.length - 1 &&
                    handleSelectSceneFreeOrder(imageSegments[activeIdx + 1])
                  }
                  className="w-11 h-11 rounded-full border flex items-center justify-center cursor-pointer disabled:opacity-35"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <ChevronLeft className="w-5 h-5" />
                </button>
              </div>
            )}

            {/* Auto-scrolling Bottom Thumbnail Strip with Clear Animated Badges */}
            <div
              className="p-2.5 rounded-xl border flex gap-2.5 overflow-x-auto smooth-scroll-x"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              {imageSegments.map((img) => {
                const a = assignments[img.id];
                const tMeta = a ? MOTION_TABS.find((t) => t.id === a.type) : null;
                const isSel = activeImage?.id === img.id;
                return (
                  <button
                    key={img.id}
                    ref={(el) => {
                      thumbRefs.current[img.id] = el;
                    }}
                    onClick={() => handleSelectSceneFreeOrder(img)}
                    className="relative w-28 h-16 rounded-lg shrink-0 border-2 overflow-hidden flex flex-col items-center justify-center text-white text-xs font-bold cursor-pointer transition-transform"
                    style={{
                      background: img.color,
                      borderColor: isSel ? 'var(--accent)' : a ? 'var(--success-text)' : 'var(--border)',
                    }}
                  >
                    <span className="tabular-nums">#{img.id}</span>
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded mt-0.5"
                      style={{
                        background: a ? 'rgba(87,211,155,0.22)' : 'rgba(0,0,0,0.45)',
                        color: a ? '#A8F5CF' : '#CBD5E1',
                      }}
                    >
                      {tMeta ? `${tMeta.icon} متحرك` : 'بانتظار'}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Stage 5: Two-Stage Export Report & Independent Per-Scene Render */}
        {stage === 'export' && (
          <div className="space-y-5">
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">5. التصدير على مرحلتين والتقرير النهائي</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  المرحلة 1 (قص الصوت) مستقلة تماماً عن المرحلة 2 (تجهيز كل مشهد على حدة) بوضع هادئ للملفات الطويلة.
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={() => setStage('motion')}
                  className="px-4 py-2 rounded-xl border text-sm cursor-pointer"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  تعديل الحركات
                </button>
                <button
                  disabled={jobActive}
                  onClick={() => runTwoStageExport(true)}
                  className="px-5 py-2 rounded-xl font-semibold inline-flex items-center gap-2 cursor-pointer disabled:opacity-50"
                  style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
                >
                  <Play className="w-4 h-4" />
                  تشغيل المرحلتين الآن
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>المرحلة 1: المقاطع الصوتية</div>
                <div className="text-2xl font-bold tabular-nums mt-1">{slicedCount} / {segments.length}</div>
              </div>
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>المرحلة 2: المشاهد الجاهزة</div>
                <div className="text-2xl font-bold tabular-nums mt-1">{renderedCount} / {segments.length}</div>
              </div>
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>وضع الموارد</div>
                <div className="text-xl font-bold mt-1" style={{ color: 'var(--accent)' }}>
                  {RESOURCE_MODES.find((m) => m.id === resourceMode)?.label}
                </div>
              </div>
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>حالة التطابق</div>
                <div className="text-lg font-bold mt-1 flex items-center gap-1.5" style={{ color: 'var(--success-text)' }}>
                  <Check className="w-5 h-5" />
                  صفر فجوات
                </div>
              </div>
            </div>

            {/* Independent Per-Scene Cards */}
            <div
              className="p-5 rounded-2xl border space-y-3"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <div className="flex items-center justify-between flex-wrap gap-2">
                <h2 className="font-bold text-base">حالة كل مشهد على حدة (استقلالية تامة)</h2>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  يمكنك إعادة تجهيز أي مشهد منفرداً دون إعادة المشروع بالكامل
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {segments.map((s) => {
                  const clip = slicedClips[s.id];
                  const isReady = !!renderedScenes[s.id];
                  const assign = assignments[s.id];
                  return (
                    <div
                      key={s.id}
                      className="p-3.5 rounded-xl border flex flex-col justify-between gap-2.5 text-xs"
                      style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                    >
                      <div className="flex items-center justify-between font-bold">
                        <span>مشهد #{s.id} · {s.sceneFile}</span>
                        <span style={{ color: isReady ? 'var(--success-text)' : 'var(--text-muted)' }}>
                          {isReady ? 'مجهّز ✓' : 'بانتظار'}
                        </span>
                      </div>
                      <div style={{ color: 'var(--text-muted)' }}>
                        المدة: {(s.end - s.start).toFixed(2)}ث ·{' '}
                        {assign
                          ? `حركة: ${MOTION_TABS.find((t) => t.id === assign.type)?.label}`
                          : s.sceneType === 'video'
                            ? 'مقطع فيديو صامت'
                            : 'صورة ثابتة'}
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <button
                          onClick={() => renderSingleScene(s.id)}
                          className="px-2.5 py-1 rounded-lg border font-semibold inline-flex items-center gap-1 cursor-pointer"
                          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                        >
                          <RefreshCw className="w-3 h-3" />
                          {isReady ? 'إعادة تجهيز' : 'تجهيز المشهد'}
                        </button>
                        {clip && (
                          <a
                            href={clip.url}
                            download={`clip_${String(s.id).padStart(3, '0')}.wav`}
                            className="px-2.5 py-1 rounded-lg border font-semibold inline-flex items-center gap-1"
                            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                          >
                            <Download className="w-3 h-3" />
                            تنزيل الصوت
                          </a>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div
              className="p-5 rounded-2xl border space-y-2 text-sm"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <div className="flex items-center gap-2 font-semibold text-base">
                <Play className="w-4 h-4" style={{ color: 'var(--accent)' }} />
                التصدير الكامل لملف MP4 محلياً عبر FFmpeg
              </div>
              <p style={{ color: 'var(--text-muted)' }}>
                على المتصفح يمكنك تشغيل المعاينة الفورية للمشاهد وقص وتنزيل كل مقطع صوتي (<span dir="ltr" className="font-mono">.wav</span>) أوفلاين. ولدمج الفيديو النهائي بدقة كاملة <span dir="ltr" className="font-mono">1080p MP4</span> بحدود معالج حقيقية (<span dir="ltr" className="font-mono">threads=2</span> و <span dir="ltr" className="font-mono">workers=1</span>)، نزّل <span dir="ltr" className="font-mono">autocut-windows.zip</span> وشغّله عبر <span dir="ltr" className="font-mono">http://127.0.0.1:8000</span>.
              </p>
            </div>
          </div>
        )}
      </main>

      {/* Windows Installation & PWA Guide Modal */}
      {showInstallModal && (
        <div
          className="fixed inset-0 z-50 bg-black/65 flex items-center justify-center p-4"
          onClick={() => setShowInstallModal(false)}
        >
          <div
            className="w-full max-w-lg rounded-2xl border p-6 space-y-4"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">تثبيت وتشغيل AutoCut v1.3.0 محلياً</h3>
              <button
                onClick={() => setShowInstallModal(false)}
                className="p-1.5 rounded-lg border cursor-pointer"
                style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <ol className="space-y-3 text-sm leading-6 list-decimal list-inside" style={{ color: 'var(--text-primary)' }}>
              <li>
                اضغط زر <strong>تنزيل حزمة AutoCut (.zip)</strong> ثم فك الضغط عنها في أي مجلد (مثلاً <span dir="ltr" className="font-mono">C:\autocut</span>).
              </li>
              <li>
                تأكد من وجود <strong>Python 3.11+</strong> على ويندوز مع تفعيل خيار <span dir="ltr" className="font-mono">Add Python to PATH</span>.
              </li>
              <li>
                اضغط مرتين على <span dir="ltr" className="font-mono font-semibold">install.bat</span> ثم شغّل <span dir="ltr" className="font-mono font-semibold">run.bat</span> لفتح التطبيق عبر <span dir="ltr" className="font-mono">http://127.0.0.1:8000</span>.
              </li>
              <li>
                للتثبيت كتطبيق مستقل (<span dir="ltr" className="font-mono">PWA</span>) بدون إنترنت، افتحه عبر <span dir="ltr" className="font-mono">localhost</span> أو <span dir="ltr" className="font-mono">HTTPS</span> (لا تعمل ميزة التثبيت عند فتح الملف مباشرة عبر <span dir="ltr" className="font-mono">file://</span>) واضغط <strong>تثبيت التطبيق</strong>.
              </li>
            </ol>

            <div className="flex items-center justify-end gap-2 pt-2">
              <a
                href="/autocut-windows.zip"
                download="autocut-windows.zip"
                className="px-4 py-2 rounded-xl text-sm font-semibold inline-flex items-center gap-2"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                <Download className="w-4 h-4" />
                تنزيل الحزمة الآن (.zip)
              </a>
            </div>
          </div>
        </div>
      )}

      {/* iOS Safari Add-to-Home-Screen Instructions Modal */}
      {showIOSGuide && (
        <div
          className="fixed inset-0 z-50 bg-black/65 flex items-center justify-center p-4"
          onClick={() => setShowIOSGuide(false)}
        >
          <div
            className="w-full max-w-sm rounded-2xl border p-6 space-y-4"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-bold">تثبيت AutoCut على iPhone / iPad</h3>
            <p className="text-sm leading-6" style={{ color: 'var(--text-muted)' }}>
              1. اضغط على زر <strong>المشاركة (Share)</strong> أسفل متصفح Safari.<br />
              2. مرّر للأسفل واختر <strong>إضافة إلى الشاشة الرئيسية (Add to Home Screen)</strong>.
            </p>
            <button
              onClick={() => setShowIOSGuide(false)}
              className="w-full py-2 rounded-xl text-sm font-semibold cursor-pointer"
              style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
            >
              حسناً
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
