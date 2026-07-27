import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Popconfirm from '../Popconfirm'

beforeEach(() => {
  document.body.innerHTML = ''
})

it('renders trigger element', () => {
  render(
    <Popconfirm title="确认删除？">
      <button>删除</button>
    </Popconfirm>,
  )
  expect(screen.getByText('删除')).toBeTruthy()
})

it('shows popconfirm on trigger click', async () => {
  const user = userEvent.setup()
  render(
    <Popconfirm title="确认删除？">
      <button>删除</button>
    </Popconfirm>,
  )
  await user.click(screen.getByText('删除'))
  expect(screen.getByText('确认删除？')).toBeTruthy()
})

it('calls onConfirm and closes when ok clicked', async () => {
  const onConfirm = vi.fn()
  const user = userEvent.setup()
  render(
    <Popconfirm title="确认？" onConfirm={onConfirm}>
      <button>删除</button>
    </Popconfirm>,
  )
  await user.click(screen.getByText('删除'))
  await user.click(screen.getByText('确定'))
  expect(onConfirm).toHaveBeenCalledTimes(1)
  expect(screen.queryByText('确认？')).toBeNull()
})

it('calls onCancel and closes when cancel clicked', async () => {
  const onCancel = vi.fn()
  const user = userEvent.setup()
  render(
    <Popconfirm title="确认？" onCancel={onCancel}>
      <button>删除</button>
    </Popconfirm>,
  )
  await user.click(screen.getByText('删除'))
  await user.click(screen.getByText('取消'))
  expect(onCancel).toHaveBeenCalledTimes(1)
  expect(screen.queryByText('确认？')).toBeNull()
})

it('renders description when provided', async () => {
  const user = userEvent.setup()
  render(
    <Popconfirm title="确认？" description="此操作不可撤销">
      <button>删除</button>
    </Popconfirm>,
  )
  await user.click(screen.getByText('删除'))
  expect(screen.getByText('此操作不可撤销')).toBeTruthy()
})

it('closes on Escape', async () => {
  const user = userEvent.setup()
  render(
    <Popconfirm title="确认？">
      <button>删除</button>
    </Popconfirm>,
  )
  await user.click(screen.getByText('删除'))
  expect(screen.getByText('确认？')).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByText('确认？')).toBeNull()
})

it('closes on outside click', async () => {
  const user = userEvent.setup()
  render(
    <div>
      <Popconfirm title="确认？">
        <button>删除</button>
      </Popconfirm>
      <div data-testid="outside">outside</div>
    </div>,
  )
  await user.click(screen.getByText('删除'))
  expect(screen.getByText('确认？')).toBeTruthy()
  await user.click(screen.getByTestId('outside'))
  expect(screen.queryByText('确认？')).toBeNull()
})

it('renders custom okText and cancelText', async () => {
  const user = userEvent.setup()
  render(
    <Popconfirm title="确认？" okText="是的" cancelText="不了">
      <button>删除</button>
    </Popconfirm>,
  )
  await user.click(screen.getByText('删除'))
  expect(screen.getByText('是的')).toBeTruthy()
  expect(screen.getByText('不了')).toBeTruthy()
})
