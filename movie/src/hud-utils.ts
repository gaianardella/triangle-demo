export const seededRand = (frame: number, seed: number): number => {
  const x = Math.sin(frame * seed + seed * 13.37) * 10000;
  return x - Math.floor(x);
};
