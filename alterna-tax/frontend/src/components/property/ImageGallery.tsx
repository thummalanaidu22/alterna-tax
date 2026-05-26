import { useState } from "react";
import { X, ImageOff, Satellite, MapPin } from "lucide-react";

interface ImageGalleryProps {
  jobId: string;
  streetViewCount?: number;
}

const BACKEND = "http://localhost:8000";

const IMAGES = [
  { key: "satellite", label: "Satellite", icon: <Satellite className="w-5 h-5" />, path: (id: string) => `${BACKEND}/images/satellite/${id}_satellite.jpg` },
  { key: "center",    label: "Street: Center", icon: <MapPin className="w-5 h-5" />, path: (id: string) => `${BACKEND}/images/street/${id}_sv_center.jpg` },
  { key: "left",      label: "Street: Left",   icon: <MapPin className="w-5 h-5" />, path: (id: string) => `${BACKEND}/images/street/${id}_sv_left.jpg` },
  { key: "right",     label: "Street: Right",  icon: <MapPin className="w-5 h-5" />, path: (id: string) => `${BACKEND}/images/street/${id}_sv_right.jpg` },
];

function ImageTile({ label, src, icon, onOpen }: { label: string; src: string; icon: React.ReactNode; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  return (
    <div
      className={`relative group rounded-lg overflow-hidden bg-gray-800/60 aspect-video border border-gray-700/50 ${!failed ? "cursor-zoom-in" : ""}`}
      onClick={() => !failed && onOpen()}
    >
      {!failed ? (
        <>
          {!loaded && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 animate-pulse">
              <div className="text-gray-600">{icon}</div>
              <span className="text-xs text-gray-600">Loading {label}…</span>
            </div>
          )}
          <img
            src={src}
            alt={label}
            className={`w-full h-full object-cover group-hover:scale-105 transition-all duration-200 ${loaded ? "opacity-100" : "opacity-0"}`}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
          />
          {loaded && (
            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 px-2 py-1">
              <span className="text-xs text-white/80">{label}</span>
            </div>
          )}
        </>
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-gray-600">
          <ImageOff className="w-6 h-6" />
          <span className="text-xs">{label}</span>
          <span className="text-xs text-gray-700">Not available</span>
        </div>
      )}
    </div>
  );
}

export function ImageGallery({ jobId, streetViewCount }: ImageGalleryProps) {
  const [lightbox, setLightbox] = useState<string | null>(null);

  // Only render street view tiles when we know images were actually captured.
  // streetViewCount undefined = old job or unknown → show all (may 404 gracefully).
  const visibleImages = IMAGES.filter(({ key }) => {
    if (key === "satellite") return true;
    if (streetViewCount === 0) return false;
    return true;
  });

  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        {streetViewCount === 0 && (
          <div className="col-span-2 text-xs text-gray-600 italic px-1 pb-1">
            No Street View coverage found for this property
          </div>
        )}
        {visibleImages.map(({ key, label, icon, path }) => (
          <ImageTile
            key={key}
            label={label}
            src={path(jobId)}
            icon={icon}
            onOpen={() => setLightbox(path(jobId))}
          />
        ))}
      </div>

      {lightbox && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
          onClick={() => setLightbox(null)}
        >
          <button
            className="absolute top-4 right-4 text-white/70 hover:text-white"
            onClick={() => setLightbox(null)}
          >
            <X className="w-8 h-8" />
          </button>
          <img
            src={lightbox}
            alt="Full size"
            className="max-w-full max-h-full object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}
