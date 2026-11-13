const imagePath = /^\/api\/images\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/content$/

// Apply to every render path, including references and user-uploaded images.
export function isImageResource(url: string) { return imagePath.test(url) }
