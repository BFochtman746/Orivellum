// Manual mock for expo-audio — used in unit tests only.
// Replaced by jest.mock() calls in individual test files when more control
// over player behaviour is needed.
const createAudioPlayer = jest.fn(() => ({
  play: jest.fn(),
  pause: jest.fn(),
  remove: jest.fn(),
  addListener: jest.fn(() => ({ remove: jest.fn() })),
}));
const setAudioModeAsync = jest.fn(() => Promise.resolve());

module.exports = { createAudioPlayer, setAudioModeAsync };
