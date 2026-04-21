import { VideoItem } from "../api/client";
import VideoCard from "./VideoCard";

type VideoGridProps = {
  videos: VideoItem[];
  minCardWidth?: number;
  showPath?: boolean;
  showExtraMeta?: boolean;
  activeTags?: Set<string>;
  onTagClick?: (tag: string) => void;
};

export default function VideoGrid({
  videos,
  minCardWidth = 260,
  showPath = false,
  showExtraMeta = false,
  activeTags,
  onTagClick,
}: VideoGridProps) {
  return (
    <div
      className="grid gap-6"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${minCardWidth}px, 1fr))` }}
    >
      {videos.map((video) => (
        <VideoCard
          key={video.id}
          video={video}
          showPath={showPath}
          showExtraMeta={showExtraMeta}
          activeTags={activeTags}
          onTagClick={onTagClick}
        />
      ))}
    </div>
  );
}
