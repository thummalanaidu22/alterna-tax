import { useState } from "react";
import { X } from "lucide-react";

interface ImageGalleryProps {
  jobId: string;
  satellite?: boolean;
  center?: boolean;
  left?: boolean;
  right?: boolean;
}

const IMAGES = [
  { key: "satellite", label: "Satellite", path: (id: string) => `/images/satellite/${id}_satellite.jpg` },
  { key: "center", label: "Street: Center", path: (id: string) => `/images/street/${id}_sv_center.jpg` },
  { key: "left", label: "Street: Left", path: (id: string) => `/images/street/${id}_sv_left.jpg` },
  { key: "right", label: "Street: Right", path: (id: string) => `/images/street/${id}_sv_right.jpg` },
];

export function ImageGallery({ jobId }: ImageGalleryProps) {
  const [lightbox, setLightbox] = useState<string | null>(null);

  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        {IMAGES.map(({ key, label, path }) => {
          const src = path(jobId);
          return (
            <div
              key={key}
              className="relative group cursor-zoom-in rounded-lg overflow-hidden bg-gray-800 aspect-video"
              onClick={() => setLightbox(src)}
            >
              <img
                src={src}
                alt={label}
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = "none";
                  (e.target as HTMLImageElement).nextElementSibling?.classList.remove("hidden");
                }}
              />
              <div className="hidden absolute inset-0 flex items-center justify-center bg-gray-800">
                <span className="text-xs text-gray-500">{label}</span>
              </div>
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 px-2 py-1">
                <span className="text-xs text-white/80">{label}</span>
              </div>
            </div>
          );
        })}
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
