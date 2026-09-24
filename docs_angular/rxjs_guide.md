# Angular RxJS Guide

> Source: official Angular documentation (https://angular.dev), from the
> angular/angular repository at commit 28ad39af2495, `adev/src/content/`.
> Copyright (c) 2010-2026 Google LLC. Licensed under the MIT License
> (https://angular.dev/license). This is a selection of the most practical
> sections; angular.dev markup was converted to plain Markdown and relative
> links were made absolute, but the text itself is unchanged.

## RxJS interop with Angular signals

Official page: https://angular.dev/ecosystem/rxjs-interop/signals-interop

The `@angular/core/rxjs-interop` package offers APIs that help you integrate RxJS and Angular signals.

### Create a signal from an RxJs Observable with `toSignal`

Use the `toSignal` function to create a signal which tracks the value of an Observable. It behaves similarly to the `async` pipe in templates, but is more flexible and can be used anywhere in an application.

```ts
import {Component} from '@angular/core';
import {AsyncPipe} from '@angular/common';
import {interval} from 'rxjs';
import {toSignal} from '@angular/core/rxjs-interop';

@Component({
  template: `{{ counter() }}`,
})
export class Ticker {
  counterObservable = interval(1000);

  // Get a `Signal` representing the `counterObservable`'s value.
  counter = toSignal(this.counterObservable, {initialValue: 0});
}
```

Like the `async` pipe, `toSignal` subscribes to the Observable immediately, which may trigger side effects. The subscription created by `toSignal` automatically unsubscribes from the given Observable when the component or service which calls `toSignal` is destroyed.

IMPORTANT: `toSignal` creates a subscription. You should avoid calling it repeatedly for the same Observable, and instead reuse the signal it returns.

#### Injection context

`toSignal` by default needs to run in an [injection context](https://angular.dev/guide/di/dependency-injection-context), such as during construction of a component or service. If an injection context is not available, you can manually specify the `Injector` to use instead.

#### Initial values

Observables may not produce a value synchronously on subscription, but signals always require a current value. There are several ways to deal with this "initial" value of `toSignal` signals.

##### The `initialValue` option

As in the example above, you can specify an `initialValue` option with the value the signal should return before the Observable emits for the first time.

##### `undefined` initial values

If you don't provide an `initialValue`, the resulting signal will return `undefined` until the Observable emits. This is similar to the `async` pipe's behavior of returning `null`.

##### The `requireSync` option

Some Observables are guaranteed to emit synchronously, such as `BehaviorSubject`. In those cases, you can specify the `requireSync: true` option.

When `requireSync` is `true`, `toSignal` enforces that the Observable emits synchronously on subscription. This guarantees that the signal always has a value, and no `undefined` type or initial value is required.

#### `manualCleanup`

By default, `toSignal` automatically unsubscribes from the Observable when the component or service that creates it is destroyed.

To override this behavior, you can pass the `manualCleanup` option. You can use this setting for Observables that complete themselves naturally.

##### Custom equality comparison

Some observables may emit values that are **equals** even though they differ by reference or minor detail. The `equal` option lets you define a **custom equal function** to determine when two consecutive values should be considered the same.

When two emitted values are considered equal, the resulting signal **does not update**. This prevents redundant computations, DOM updates, or effects from re-running unnecessarily.

```ts
import {Component} from '@angular/core';
import {toSignal} from '@angular/core/rxjs-interop';
import {interval, map} from 'rxjs';

@Component(/* ... */)
export class EqualExample {
  temperature$ = interval(1000).pipe(
    map(() => ({temperature: Math.floor(Math.random() * 3) + 20})), // 20, 21, or 22 randomly
  );

  // Only update if the temperature changes
  temperature = toSignal(this.temperature$, {
    initialValue: {temperature: 20},
    equal: (prev, curr) => prev.temperature === curr.temperature,
  });
}
```

#### Error and Completion

If an Observable used in `toSignal` produces an error, that error is thrown when the signal is read.

If an Observable used in `toSignal` completes, the signal continues to return the most recently emitted value before completion.

### Create an RxJS Observable from a signal with `toObservable`

Use the `toObservable` utility to create an `Observable` which tracks the value of a signal. The signal's value is monitored with an `effect` which emits the value to the Observable when it changes.

```ts
import {Component, signal} from '@angular/core';
import {toObservable} from '@angular/core/rxjs-interop';

@Component(/* ... */)
export class SearchResults {
  query: Signal<string> = inject(QueryService).query;
  query$ = toObservable(this.query);

  results$ = this.query$.pipe(switchMap((query) => this.http.get('/search?q=' + query)));
}
```

As the `query` signal changes, the `query$` Observable emits the latest query and triggers a new HTTP request.

#### Injection context

`toObservable` by default needs to run in an [injection context](https://angular.dev/guide/di/dependency-injection-context), such as during construction of a component or service. If an injection context is not available, you can manually specify the `Injector` to use instead.

#### Timing of `toObservable`

`toObservable` uses an effect to track the value of the signal in a `ReplaySubject`. On subscription, the first value (if available) may be emitted synchronously, and all subsequent values will be asynchronous.

Unlike Observables, signals never provide a synchronous notification of changes. Even if you update a signal's value multiple times, `toObservable` will only emit the value after the signal stabilizes.

```ts
const obs$ = toObservable(mySignal);
obs$.subscribe((value) => console.log(value));

mySignal.set(1);
mySignal.set(2);
mySignal.set(3);
```

Here, only the last value (3) will be logged.

### Using `rxResource` for async data

Angular's [`resource` function](https://angular.dev/guide/signals/resource) gives you a way to incorporate async data into your application's signal-based code. Building on top of this pattern, `rxResource` lets you define a resource where the source of your data is defined in terms of an RxJS `Observable`. Instead of accepting a `loader` function, `rxResource` accepts a `stream` function that accepts an RxJS `Observable`.

```typescript
import {Component, inject} from '@angular/core';
import {rxResource} from '@angular/core/rxjs-interop';

@Component(/* ... */)
export class UserProfile {
  // This component relies on a service that exposes data through an RxJS Observable.
  private userData = inject(MyUserDataClient);

  protected userId = input<string>();

  private userResource = rxResource({
    params: () => ({userId: this.userId()}),

    // The `stream` property expects a factory function that returns
    // a data stream as an RxJS Observable.
    stream: ({params}) => this.userData.load(params.userId),
  });
}
```

The `stream` property accepts a factory function for an RxJS `Observable`. This factory function is passed the resource's `params` value and returns an `Observable`. The resource calls this factory function every time the `params` computation produces a new value. See [Resource loaders](https://angular.dev/guide/signals/resource#resource-loaders) for more details on the parameters passed to the factory function.

In all other ways, `rxResource` behaves like and provides the same APIs as `resource` for specifying parameters, reading values, checking loading state, and examining errors.

The `Observable` returned from `stream` must always emit a value or an error before it completes; see [`NG0991`](https://angular.dev/errors/NG0991) for what happens otherwise and how to avoid it.

## Unsubscribing with `takeUntilDestroyed`

Official page: https://angular.dev/ecosystem/rxjs-interop/take-until-destroyed

TIP: This guide assumes you're familiar with [component and directive lifecycle](https://angular.dev/guide/components/lifecycle).

The `takeUntilDestroyed` operator, from `@angular/core/rxjs-interop`, provides a concise and reliable way to automatically unsubscribe from an Observable when a component or directive is destroyed. This prevents common memory leaks with RxJS subscriptions. It works similarly to the RxJS [`takeUntil`](https://rxjs.dev/api/operators/takeUntil) operator but without the need for a separate Subject.

```typescript
import {Component, inject} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {NotificationDispatcher, CustomPopupShower} from './some-shared-project-code';

@Component(/* ... */)
export class UserProfile {
  private dispatcher = inject(NotificationDispatcher);
  private popup = inject(CustomPopupShower);

  constructor() {
    // This subscription the 'notifications' Observable is automatically
    // unsubscribed when the 'UserProfile' component is destroyed.
    const messages: Observable<string> = this.dispatcher.notifications;
    messages.pipe(takeUntilDestroyed()).subscribe((message) => {
      this.popup.show(message);
    });
  }
}
```

The `takeUntilDestroyed` operator accepts a single optional [`DestroyRef`](https://angular.dev/api/core/DestroyRef) argument. The operator uses `DestroyRef` to know when the component or directive has been destroyed. You can omit this argument when calling `takeUntilDestroyed` in an [injection context](https://angular.dev/guide/di/dependency-injection-context), typically the constructor of a component or directive. Always provide a `DestroyRef` if your code may call `takeUntilDestroyed` outside of an injection context.

```typescript
@Component(/* ... */)
export class UserProfile {
  private dispatcher = inject(NotificationDispatcher);
  private popup = inject(CustomPopupShower);
  private destroyRef = inject(DestroyRef);

  startListeningToNotifications() {
    // Always pass a `DestroyRef` if you call `takeUntilDestroyed` outside
    // of an injection context.
    const messages: Observable<string> = this.dispatcher.notifications;
    messages.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((message) => {
      this.popup.show(message);
    });
  }
}
```

## HttpClient and Observables

Official page: https://angular.dev/guide/http/making-requests

`HttpClient` has methods corresponding to the different HTTP verbs used to make requests, both to load data and to apply mutations on the server. Each method returns an [RxJS `Observable`](https://rxjs.dev/guide/observable) which, when subscribed, sends the request and then emits the results when the server responds.

NOTE: `Observable`s created by `HttpClient` may be subscribed any number of times and will make a new backend request for each subscription.

### HTTP `Observable`s

Each request method on `HttpClient` constructs and returns an `Observable` of the requested response type. Understanding how these `Observable`s work is important when using `HttpClient`.

`HttpClient` produces what RxJS calls "cold" `Observable`s, meaning that no actual request happens until the `Observable` is subscribed. Only then is the request actually dispatched to the server. Subscribing to the same `Observable` multiple times will trigger multiple backend requests. Each subscription is independent.

TIP: You can think of `HttpClient` `Observable`s as _blueprints_ for actual server requests.

Once subscribed, unsubscribing will abort the in-progress request. This is very useful if the `Observable` is subscribed via the `async` pipe, as it will automatically cancel the request if the user navigates away from the current page. Additionally, if you use the `Observable` with an RxJS combinator like `switchMap`, this cancellation will clean up any stale requests.

Once the response returns, `Observable`s from `HttpClient` usually complete (although interceptors can influence this).

Because of the automatic completion, there is usually no risk of memory leaks if `HttpClient` subscriptions are not cleaned up. However, as with any async operation, we strongly recommend that you clean up subscriptions when the component using them is destroyed, as the subscription callback may otherwise run and encounter errors when it attempts to interact with the destroyed component.

TIP: Using the `async` pipe or the `toSignal` operation to subscribe to `Observable`s ensures that subscriptions are disposed properly.


### Handling request failure

There are three ways an HTTP request can fail:

- A network or connection error can prevent the request from reaching the backend server.
- A request didn't respond in time when the timeout option was set.
- The backend can receive the request but fail to process it, and return an error response.

`HttpClient` captures all of the above kinds of errors in an `HttpErrorResponse` which it returns through the `Observable`'s error channel. Network and timeout errors have a `status` code of `0` and an `error` which is an instance of [`ProgressEvent`](https://developer.mozilla.org/docs/Web/API/ProgressEvent). Backend errors have the failing `status` code returned by the backend, and the error response as the `error`. Inspect the response to identify the error's cause and the appropriate action to handle the error.

The [RxJS library](https://rxjs.dev/) offers several operators which can be useful for error handling.

You can use the `catchError` operator to transform an error response into a value for the UI. This value can tell the UI to display an error page or value, and capture the error's cause if necessary.

Sometimes transient errors such as network interruptions can cause a request to fail unexpectedly, and simply retrying the request will allow it to succeed. RxJS provides several _retry_ operators which automatically re-subscribe to a failed `Observable` under certain conditions. For example, the `retry()` operator will automatically attempt to re-subscribe a specified number of times.

#### Timeouts

To set a timeout for a request, you can set the `timeout` option to a number of milliseconds along with other request options. If the backend request does not complete within the specified time, the request will be aborted and an error will be emitted.

NOTE: The timeout will only apply to the backend HTTP request itself. It is not a timeout for the entire request handling chain. Therefore, this option is not affected by any delay introduced by interceptors.

```ts
http
  .get('/api/config', {
    timeout: 3000,
  })
  .subscribe({
    next: (config) => {
      console.log('Config fetched successfully:', config);
    },
    error: (err) => {
      // If the request times out, an error will have been emitted.
    },
  });
```


### Best practices

While `HttpClient` can be injected and used directly from components, generally we recommend you create reusable, injectable services which isolate and encapsulate data access logic. For example, this `UserService` encapsulates the logic to request data for a user by their id:

```ts
@Service()
export class UserService {
  private http = inject(HttpClient);

  getUser(id: string): Observable<User> {
    return this.http.get<User>(`/api/user/${id}`);
  }
}
```

Within a component, you can combine `@if` with the `async` pipe to render the UI for the data only after it's finished loading:

```ts
import {AsyncPipe} from '@angular/common';

@Component({
  imports: [AsyncPipe],
  template: `
    @if (user$ | async; as user) {
      <p>Name: {{ user.name }}</p>
      <p>Biography: {{ user.biography }}</p>
    }
  `,
})
export class UserProfile {
  userId = input.required<string>();
  user$!: Observable<User>;

  private userService = inject(UserService);

  constructor(): void {
    effect(() => {
      this.user$ = this.userService.getUser(this.userId());
    });
  }
}
```

## HTTP interceptors

Official page: https://angular.dev/guide/http/interceptors

`HttpClient` supports a form of middleware known as _interceptors_.

TLDR: Interceptors are middleware that allows common patterns around retrying, caching, logging, and authentication to be abstracted away from individual requests.

`HttpClient` supports two kinds of interceptors: functional and DI-based. Our recommendation is to use functional interceptors because they have more predictable behavior, especially in complex setups. Our examples in this guide use functional interceptors, and we cover [DI-based interceptors](https://angular.dev/guide/http/interceptors#di-based-interceptors) in their own section at the end.

### Defining an interceptor

The basic form of an interceptor is a function which receives the outgoing `HttpRequest` and a `next` function representing the next processing step in the interceptor chain.

For example, this `loggingInterceptor` will log the outgoing request URL to `console.log` before forwarding the request:

```ts
export function loggingInterceptor(
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
): Observable<HttpEvent<unknown>> {
  console.log(req.url);
  return next(req);
}
```

In order for this interceptor to actually intercept requests, you must configure `HttpClient` to use it.


### Configuring interceptors

You declare the set of interceptors to use when configuring `HttpClient` through dependency injection, by using the `withInterceptors` feature:

```ts
bootstrapApplication(App, {
  providers: [provideHttpClient(withInterceptors([loggingInterceptor, cachingInterceptor]))],
});
```

The interceptors you configure are chained together in the order that you've listed them in the providers. In the above example, the `loggingInterceptor` would process the request and then forward it to the `cachingInterceptor`.

#### Intercepting response events

An interceptor may transform the `Observable` stream of `HttpEvent`s returned by `next` in order to access or manipulate the response. Because this stream includes all response events, inspecting the `.type` of each event may be necessary in order to identify the final response object.

```ts
export function loggingInterceptor(
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
): Observable<HttpEvent<unknown>> {
  return next(req).pipe(
    tap((event) => {
      if (event.type === HttpEventType.Response) {
        console.log(req.url, 'returned a response with status', event.status);
      }
    }),
  );
}
```

TIP: Interceptors naturally associate responses with their outgoing requests, because they transform the response stream in a closure that captures the request object.


### Modifying requests

Most aspects of `HttpRequest` and `HttpResponse` instances are _immutable_, and interceptors cannot directly modify them. Instead, interceptors apply mutations by cloning these objects using the `.clone()` operation, and specifying which properties should be mutated in the new instance. This might involve performing immutable updates on the value itself (like `HttpHeaders` or `HttpParams`).

For example, to add a header to a request:

```ts
const reqWithHeader = req.clone({
  headers: req.headers.set('X-New-Header', 'new header value'),
});
```

This immutability allows most interceptors to be idempotent if the same `HttpRequest` is submitted to the interceptor chain multiple times. This can happen for a few reasons, including when a request is retried after failure.

CRITICAL: The body of a request or response is **not** protected from deep mutations. If an interceptor must mutate the body, take care to handle running multiple times on the same request.
