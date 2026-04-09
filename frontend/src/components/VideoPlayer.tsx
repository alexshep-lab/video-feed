type VideoPlayerProps = {
  src: string;
};

export default function VideoPlayer({ src }: VideoPlayerProps) {
  return (
    <div className="overflow-hidden rounded-[2rem] border border-white/10 bg-black shadow-card">
      <video
        src={src}
        controls
        preload="metadata"
        className="max-h-[75vh] w-full bg-black"
      />
    </div>
  );
}
