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
