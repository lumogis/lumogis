-- LUM-540 — cursor integration fixture user email used @test.lumogis.local, which
-- Pydantic EmailStr rejects when admin list_users hydrates InternalUser rows.
UPDATE users
SET email = 'cursor-integration-full@example.com'
WHERE id = 'cursor-integration-full'
  AND email = 'cursor-integration-full@test.lumogis.local';
