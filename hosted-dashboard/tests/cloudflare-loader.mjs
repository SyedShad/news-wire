const cloudflareWorkersModule = `
export const env = new Proxy({}, {
  get(_target, property) {
    return globalThis.__CLOUDFLARE_TEST_ENV__?.[property];
  }
});
`;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return {
      url: `data:text/javascript,${encodeURIComponent(cloudflareWorkersModule)}`,
      shortCircuit: true,
    };
  }
  return nextResolve(specifier, context);
}
