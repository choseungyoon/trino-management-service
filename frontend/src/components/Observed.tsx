/**
 * The sentence under a health test's name.
 *
 * The words come from the server, which owns the test catalog — a client that
 * has never heard of H-03 must not be the thing deciding how H-03 reads. All
 * this does is put the emphasis back: the numbers are what the eye should
 * land on.
 */
export interface Segment {
  text: string;
  strong: boolean;
}

export function Observed({ segments }: { segments: Segment[] | undefined }) {
  if (!segments?.length) return <span className="dim">no reading</span>;
  return (
    <>
      {segments.map((part, index) =>
        part.strong ? <b key={index}>{part.text}</b> : <span key={index}>{part.text}</span>,
      )}
    </>
  );
}
