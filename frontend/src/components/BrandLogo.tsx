import { useState } from 'react'

// Served from frontend/public/brand/, copied verbatim from the approved
// Axyntro Violet-Spark Logo Package (frontend/brandassets/axyntro_logo_package)
// -- per that package's brand guide: colour logo on white/Cloud, white logo
// on Midnight Navy or photography, symbol-only for compact nav spots.
const SOURCES = {
  full: {
    color: '/brand/axyntro-horizontal-violet-spark.png',
    white: '/brand/axyntro-horizontal-violet-spark-white.png',
  },
  mark: {
    color: '/brand/axyntro-symbol-violet-spark.png',
    white: '/brand/axyntro-symbol-violet-spark-white.png',
  },
} as const

type Variant = keyof typeof SOURCES
type Tone = keyof (typeof SOURCES)['full']

interface BrandLogoProps {
  variant?: Variant
  tone?: Tone
  className?: string
}

export default function BrandLogo({ variant = 'full', tone = 'color', className }: BrandLogoProps) {
  const [failed, setFailed] = useState(false)
  const classes = ['brand-logo', `brand-logo-${variant}`, className].filter(Boolean).join(' ')

  if (failed) {
    return <span className={`${classes} brand-wordmark`}>Axyntro</span>
  }

  return <img src={SOURCES[variant][tone]} alt="Axyntro" className={classes} onError={() => setFailed(true)} />
}
