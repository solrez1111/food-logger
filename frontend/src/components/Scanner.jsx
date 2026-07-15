import React, { useEffect, useRef, useState } from 'react'

/* ZXing continuous scan (the Phase 0 spike winner). The spike showed decode is
   instant once framed and AIMING is the friction — hence the reticle, the
   torch toggle, and haptics on hit. Library is lazy-loaded so the main bundle
   stays lean. */
export default function Scanner({ onCode, onClose }) {
  const videoRef = useRef(null)
  const controlsRef = useRef(null)
  const trackRef = useRef(null)
  const [error, setError] = useState(null)
  const [torch, setTorch] = useState(false)
  const [torchAvailable, setTorchAvailable] = useState(false)
  const hitRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const { BrowserMultiFormatReader } = await import('@zxing/browser')
        const reader = new BrowserMultiFormatReader()
        const controls = await reader.decodeFromConstraints(
          { video: { facingMode: 'environment' } },
          videoRef.current,
          (result) => {
            if (result && !hitRef.current) {
              hitRef.current = true          // first hit wins; stop double-fires
              navigator.vibrate?.(100)
              onCode(result.getText())
            }
          }
        )
        if (cancelled) { controls.stop(); return }
        controlsRef.current = controls
        const stream = videoRef.current?.srcObject
        const track = stream?.getVideoTracks?.()[0]
        trackRef.current = track
        setTorchAvailable(!!track?.getCapabilities?.().torch)
      } catch (e) {
        if (!cancelled) setError(String(e?.message ?? e))
      }
    })()
    return () => {
      cancelled = true
      controlsRef.current?.stop()
    }
  }, [onCode])

  const toggleTorch = async () => {
    try {
      await trackRef.current?.applyConstraints({ advanced: [{ torch: !torch }] })
      setTorch(!torch)
    } catch { /* some devices refuse — button just does nothing */ }
  }

  // tap-to-focus where supported (iOS mostly handles continuous AF itself)
  const tapFocus = async () => {
    try {
      await trackRef.current?.applyConstraints({ advanced: [{ focusMode: 'continuous' }] })
    } catch { /* not supported — fine */ }
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="row spread" style={{ marginBottom: 10 }}>
          <b>Scan barcode</b>
          <div className="row">
            {torchAvailable && (
              <button className="btn secondary small" onClick={toggleTorch}>
                {torch ? 'Torch off' : 'Torch'}
              </button>
            )}
            <button className="btn secondary small" onClick={onClose}>Close</button>
          </div>
        </div>
        {error
          ? <div className="error">Camera failed: {error}</div>
          : (
            <div className="reticle-wrap" onClick={tapFocus}>
              <video ref={videoRef} className="scanner-video" playsInline muted />
              <div className="reticle" />
            </div>
          )}
        <p className="faint" style={{ marginTop: 8 }}>Line the barcode up inside the frame.</p>
      </div>
    </div>
  )
}
