// Minimal jest setup for TtsContext unit tests.
// Replaces the RN 0.81 ESM setup file (react-native/jest/setup.js) which
// cannot be parsed by jest@29 without experimental VM modules enabled.
global.IS_REACT_ACT_ENVIRONMENT = true;
global.__DEV__ = true;
