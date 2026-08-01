---
name: Mobile app improvements
description: Key decisions and patterns for the Orivellum mobile app (Expo)
---

## Mobile tab structure
Tabs: Dashboard (index), Chats (conversations), Works, Library (new)
- `_layout.tsx` supports both NativeTabs (iOS Liquid Glass) and classic Tabs
- Adding a new tab requires updating BOTH NativeTabs and ClassicTabLayout sections

## Mobile library screen
- `/app/(tabs)/library.tsx` — uses `useListLibrary` + `useSearchLibrary` (search enabled when query.length > 1)
- DocItem `onPress` shows Alert with doc details (no mobile doc detail screen yet)
- `listData.count` is the total count field (not `total`)

## Mobile markdown rendering
- Installed `react-native-markdown-display` in mobile app
- Used in `MessageBubble` for AI (non-user, non-error) messages in `chat/[id].tsx`
- User messages and error messages still use plain `<Text>` for simplicity
- `isDark` prop controls code block colors (dark zinc shades vs. light)

## Mobile model picker
- In `chat/[id].tsx` — uses `useGetSystemModels` + `useUpdateConversation`
- iOS: ActionSheetIOS.showActionSheetWithOptions
- Android/web: custom Modal with ScrollView of model options
- Shows current model label in a badge row below the header

## Mobile home dashboard improvements
- `useGetBriefing` powers the greeting subtitle (replaces hardcoded "Your research workspace")
- ActivityRow is now tappable: work → `/work/:id`, conversation → `/chat/:id`

## Works conv_count
- Added `conv_count` subquery to `list_works()` SQL in `db.py` (was already in `get_work`)
- Mobile WorkCard footer shows "N chats" chip when conv_count > 0
- Mobile WorkDetailScreen stats array includes `conv_count`
- Web works/index.tsx shows "Chats" stat column in work cards

## Key: conv_count is now in list_works
- `(SELECT COUNT(*) FROM conversations c WHERE c.work_id=w.id) as conv_count` added to list_works query
- Frontend must cast as `(work as any).conv_count` since TypeScript types haven't been regenerated
