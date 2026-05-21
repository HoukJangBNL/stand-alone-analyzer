import { expectTypeOf, describe, it } from 'vitest'
import type { ClusteringRefitBody } from '@/api/clustering'

describe('ClusteringRefitBody', () => {
  it('accepts reg_covar and auto_tune', () => {
    expectTypeOf<ClusteringRefitBody>().toMatchTypeOf<{
      reg_covar?: number
      auto_tune?: boolean
    }>()
  })
})
