// Stub for expo/virtual/env — babel-preset-expo rewrites EXPO_PUBLIC_* env
// accesses to imports from this module. In test environments we just re-expose
// process.env so the constants resolve normally.
module.exports = { env: process.env };
