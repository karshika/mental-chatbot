-- =====================================================
-- MindCare - Supabase Database Schema
-- Run this in your Supabase SQL Editor (Dashboard > SQL Editor)
-- =====================================================

-- 1. CONVERSATIONS TABLE
create table if not exists public.conversations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    title text not null default 'New conversation',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- 2. MESSAGES TABLE
create table if not exists public.messages (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references public.conversations(id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    created_at timestamptz not null default now()
);

-- 3. INDEXES for performance
create index if not exists idx_conversations_user_id on public.conversations(user_id);
create index if not exists idx_conversations_updated_at on public.conversations(updated_at desc);
create index if not exists idx_messages_conversation_id on public.messages(conversation_id);
create index if not exists idx_messages_created_at on public.messages(created_at);

-- 4. ENABLE ROW LEVEL SECURITY
alter table public.conversations enable row level security;
alter table public.messages enable row level security;

-- 5. RLS POLICIES for conversations
-- Users can only see their own conversations
create policy "Users can view own conversations"
    on public.conversations for select
    using (auth.uid() = user_id);

-- Users can create their own conversations
create policy "Users can create own conversations"
    on public.conversations for insert
    with check (auth.uid() = user_id);

-- Users can update their own conversations
create policy "Users can update own conversations"
    on public.conversations for update
    using (auth.uid() = user_id);

-- Users can delete their own conversations
create policy "Users can delete own conversations"
    on public.conversations for delete
    using (auth.uid() = user_id);

-- 6. RLS POLICIES for messages
-- Users can view messages of their own conversations
create policy "Users can view own messages"
    on public.messages for select
    using (
        conversation_id in (
            select id from public.conversations where user_id = auth.uid()
        )
    );

-- Users can insert messages into their own conversations
create policy "Users can insert own messages"
    on public.messages for insert
    with check (
        conversation_id in (
            select id from public.conversations where user_id = auth.uid()
        )
    );

-- Users can delete messages from their own conversations
create policy "Users can delete own messages"
    on public.messages for delete
    using (
        conversation_id in (
            select id from public.conversations where user_id = auth.uid()
        )
    );

-- 7. HABIT GOALS TABLE (one row per user)
create table if not exists public.habit_goals (
    user_id uuid primary key references auth.users(id) on delete cascade,
    sleep_hours_goal numeric(4,2) not null default 8.0,
    max_screen_hours_goal numeric(4,2) not null default 4.0,
    exercise_minutes_goal integer not null default 30,
    social_minutes_goal integer not null default 30,
    updated_at timestamptz not null default now()
);

-- 8. HABIT ENTRIES TABLE (one row per user per date)
create table if not exists public.habit_entries (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    entry_date date not null,
    sleep_hours numeric(4,2) not null default 0,
    screen_hours numeric(4,2) not null default 0,
    exercise_minutes integer not null default 0,
    social_minutes integer not null default 0,
    score integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint habit_entries_user_date_unique unique (user_id, entry_date)
);

-- 9. GAME STATS TABLE (one row per user per game type)
create table if not exists public.game_stats (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    game_key text not null,
    best_score integer not null default 0,
    last_score integer not null default 0,
    total_plays integer not null default 0,
    total_seconds integer not null default 0,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint game_stats_user_game_unique unique (user_id, game_key)
);

-- Optional strict key validation for the currently supported games
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'game_stats_game_key_check'
          and conrelid = 'public.game_stats'::regclass
    ) then
        alter table public.game_stats
            add constraint game_stats_game_key_check
            check (game_key in ('bubble', 'breathing', 'doodle', 'stars', 'plant'));
    end if;
end $$;

-- 10. INDEXES for habit/game tables
create index if not exists idx_habit_entries_user_date on public.habit_entries(user_id, entry_date desc);
create index if not exists idx_habit_entries_score on public.habit_entries(user_id, score);
create index if not exists idx_game_stats_user_game on public.game_stats(user_id, game_key);

-- 11. ENABLE RLS for habit/game tables
alter table if exists public.habit_goals enable row level security;
alter table if exists public.habit_entries enable row level security;
alter table if exists public.game_stats enable row level security;

-- 12. RLS POLICIES for habit_goals
do $$
begin
    if to_regclass('public.habit_goals') is not null then
        execute 'drop policy if exists "Users can view own habit goals" on public.habit_goals';
        execute 'drop policy if exists "Users can insert own habit goals" on public.habit_goals';
        execute 'drop policy if exists "Users can update own habit goals" on public.habit_goals';
        execute 'drop policy if exists "Users can delete own habit goals" on public.habit_goals';

        execute 'create policy "Users can view own habit goals" on public.habit_goals for select using (auth.uid() = user_id)';
        execute 'create policy "Users can insert own habit goals" on public.habit_goals for insert with check (auth.uid() = user_id)';
        execute 'create policy "Users can update own habit goals" on public.habit_goals for update using (auth.uid() = user_id)';
        execute 'create policy "Users can delete own habit goals" on public.habit_goals for delete using (auth.uid() = user_id)';
    end if;
end $$;

-- 13. RLS POLICIES for habit_entries
do $$
begin
    if to_regclass('public.habit_entries') is not null then
        execute 'drop policy if exists "Users can view own habit entries" on public.habit_entries';
        execute 'drop policy if exists "Users can insert own habit entries" on public.habit_entries';
        execute 'drop policy if exists "Users can update own habit entries" on public.habit_entries';
        execute 'drop policy if exists "Users can delete own habit entries" on public.habit_entries';

        execute 'create policy "Users can view own habit entries" on public.habit_entries for select using (auth.uid() = user_id)';
        execute 'create policy "Users can insert own habit entries" on public.habit_entries for insert with check (auth.uid() = user_id)';
        execute 'create policy "Users can update own habit entries" on public.habit_entries for update using (auth.uid() = user_id)';
        execute 'create policy "Users can delete own habit entries" on public.habit_entries for delete using (auth.uid() = user_id)';
    end if;
end $$;

-- 14. RLS POLICIES for game_stats
do $$
begin
    if to_regclass('public.game_stats') is not null then
        execute 'drop policy if exists "Users can view own game stats" on public.game_stats';
        execute 'drop policy if exists "Users can insert own game stats" on public.game_stats';
        execute 'drop policy if exists "Users can update own game stats" on public.game_stats';
        execute 'drop policy if exists "Users can delete own game stats" on public.game_stats';

        execute 'create policy "Users can view own game stats" on public.game_stats for select using (auth.uid() = user_id)';
        execute 'create policy "Users can insert own game stats" on public.game_stats for insert with check (auth.uid() = user_id)';
        execute 'create policy "Users can update own game stats" on public.game_stats for update using (auth.uid() = user_id)';
        execute 'create policy "Users can delete own game stats" on public.game_stats for delete using (auth.uid() = user_id)';
    end if;
end $$;
