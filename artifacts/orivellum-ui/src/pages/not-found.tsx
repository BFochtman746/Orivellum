export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center text-center space-y-4">
      <h1 className="text-6xl font-serif font-bold text-muted-foreground/30">404</h1>
      <h2 className="text-2xl font-serif font-medium">Page not found</h2>
      <p className="text-muted-foreground">The page you are looking for does not exist or has been moved.</p>
    </div>
  );
}
