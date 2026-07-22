'use client'

import { Heading, Text, Stack, Label } from '@primer/react'
import { MarkGithubIcon } from '@primer/octicons-react'
import { ColorPalette } from '@/components/showcase/color-palette'
import { TypeScale } from '@/components/showcase/type-scale'
import { ComponentShowcase } from '@/components/showcase/component-showcase'
import { CreateRepoCard } from '@/components/showcase/create-repo-card'

export default function Page() {
  return (
    <main
      style={{
        maxWidth: 960,
        margin: '0 auto',
        padding: '48px 24px 96px',
      }}
    >
      <Stack direction="vertical" gap="spacious">
        <Stack direction="vertical" gap="condensed">
          <Stack direction="horizontal" gap="condensed" align="center">
            <MarkGithubIcon size={32} />
            <Label variant="accent">Design system</Label>
          </Stack>
          <Heading as="h1" variant="large">
            Primer
          </Heading>
          <Text
            size="large"
            style={{ color: 'var(--fgColor-muted)', maxWidth: 560 }}
          >
            GitHub&apos;s design system — built with @primer/react,
            @primer/octicons-react, and @primer/primitives design tokens. This
            starter wires up the theme, tokens, and fonts so every screen
            inherits the Primer look.
          </Text>
        </Stack>

        <Divider />
        <ColorPalette />
        <Divider />
        <TypeScale />
        <Divider />
        <ComponentShowcase />
        <Divider />
        <CreateRepoCard />
      </Stack>
    </main>
  )
}

function Divider() {
  return (
    <hr
      style={{
        border: 'none',
        borderTop: 'var(--borderWidth-thin) solid var(--borderColor-default)',
        margin: 0,
      }}
    />
  )
}
