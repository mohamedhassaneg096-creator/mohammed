export function encodeAudioBufferSliceToWav(
  audioBuffer: AudioBuffer | null,
  startSec: number,
  endSec: number,
  segId: number
): Blob {
  const sampleRate = audioBuffer ? audioBuffer.sampleRate : 22050;
  const dur = Math.max(0.25, endSec - startSec);
  const startSample = Math.max(0, Math.floor(startSec * sampleRate));
  const numSamples = audioBuffer
    ? Math.max(1, Math.min(Math.floor(dur * sampleRate), Math.max(1, audioBuffer.length - startSample)))
    : Math.floor(Math.min(dur, 4.0) * sampleRate);

  const numChannels = audioBuffer ? Math.min(2, audioBuffer.numberOfChannels) : 1;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = numSamples * blockAlign;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  let offset = 44;
  if (audioBuffer) {
    const channels = Array.from({ length: numChannels }, (_, ch) => audioBuffer.getChannelData(ch));
    for (let i = 0; i < numSamples; i++) {
      const idx = startSample + i;
      for (let ch = 0; ch < numChannels; ch++) {
        const sample = Math.max(-1, Math.min(1, channels[ch][idx] ?? 0));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
      }
    }
  } else {
    // Gentle soft acoustic tone so demo clips are immediately playable in-browser
    const baseFreq = 260 + ((segId - 1) % 6) * 44;
    for (let i = 0; i < numSamples; i++) {
      const t = i / sampleRate;
      const env = Math.min(1, t / 0.04) * Math.min(1, Math.max(0, (numSamples - i) / (sampleRate * 0.08)));
      const sample =
        (Math.sin(2 * Math.PI * baseFreq * t) * 0.16 +
          Math.sin(2 * Math.PI * (baseFreq * 1.5) * t) * 0.06) *
        env;
      view.setInt16(offset, sample * 0x7fff, true);
      offset += 2;
    }
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

export async function decodeAudioFileToBuffer(file: File): Promise<{
  buffer: AudioBuffer;
  duration: number;
}> {
  const arr = await file.arrayBuffer();
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new Ctx();
  try {
    const buffer = await ctx.decodeAudioData(arr);
    return { buffer, duration: buffer.duration };
  } finally {
    ctx.close().catch(() => {});
  }
}
