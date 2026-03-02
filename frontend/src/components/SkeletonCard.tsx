/**
 * SkeletonCard -- placeholder card shown while tasks are loading.
 * Mimics the shape of a TaskCard with animated pulse effect.
 */

export default function SkeletonCard() {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 animate-pulse">
      <div className="flex items-center justify-between mb-2">
        <div className="h-3 w-16 bg-gray-200 rounded" />
        <div className="h-3 w-12 bg-gray-200 rounded" />
      </div>
      <div className="h-4 w-full bg-gray-200 rounded mb-2" />
      <div className="h-4 w-3/4 bg-gray-200 rounded mb-3" />
      <div className="h-5 w-16 bg-gray-200 rounded-full" />
    </div>
  );
}
