# Angular Standalone Components Guide

> Source: official Angular documentation (https://angular.dev), from the
> angular/angular repository at commit 30ff1317a9ad, `adev/src/content/`.
> Copyright (c) 2010-2026 Google LLC. Licensed under the MIT License
> (https://angular.dev/license). This is a selection of the most practical
> sections; angular.dev markup was converted to plain Markdown and relative
> links were made absolute, but the text itself is unchanged.

## Anatomy of a component

Official page: https://angular.dev/guide/components/anatomy-of-components




TIP: This guide assumes you've already read the [Essentials Guide](https://angular.dev/essentials). Read that first if you're new to Angular.

Every component must have:

- A TypeScript class with _behaviors_ such as handling user input and fetching data from a server
- An HTML template that controls what renders into the DOM
- A [CSS selector](https://developer.mozilla.org/docs/Learn/CSS/Building_blocks/Selectors) that defines how the component is used in HTML

You provide Angular-specific information for a component by adding a `@Component` [decorator](https://www.typescriptlang.org/docs/handbook/decorators.html) on top of the TypeScript class:

```ts
@Component({
  selector: 'profile-photo',
  template: `<img src="profile-photo.jpg" alt="Your profile photo" />`,
})
export class ProfilePhoto {}
```

For full details on writing Angular templates, including data binding, event handling, and control flow, see the [Templates guide](https://angular.dev/guide/templates).

The object passed to the `@Component` decorator is called the component's **metadata**. This includes the `selector`, `template`, and other properties described throughout this guide.

Components can optionally include a list of CSS styles that apply to that component's DOM:

```ts
@Component({
  selector: 'profile-photo',
  template: `<img src="profile-photo.jpg" alt="Your profile photo" />`,
  styles: `
    img {
      border-radius: 50%;
    }
  `,
})
export class ProfilePhoto {}
```

By default, a component's styles only affect elements defined in that component's template. See [Styling Components](https://angular.dev/guide/components/styling) for details on Angular's approach to styling.

You can alternatively choose to write your template and styles in separate files:

```ts
@Component({
  selector: 'profile-photo',
  templateUrl: 'profile-photo.html',
  styleUrl: 'profile-photo.css',
})
export class ProfilePhoto {}
```

This can help separate the concerns of _presentation_ from _behavior_ in your project. You can choose one approach for your entire project, or you can decide which to use for each component.

Both `templateUrl` and `styleUrl` are relative to the directory in which the component resides.

### Using components

#### Imports in the `@Component` decorator

To use a component, [directive](https://angular.dev/guide/directives), or [pipe](https://angular.dev/guide/templates/pipes), you must add
it to the `imports` array in the `@Component` decorator:

```ts
import {ProfilePhoto} from './profile-photo';

@Component({
  // Import the `ProfilePhoto` component in
  // order to use it in this component's template.
  imports: [ProfilePhoto],
  /* ... */
})
export class UserProfile {}
```

By default, Angular components are _standalone_, meaning that you can directly add them to the `imports` array of other components. Components created with an earlier version of Angular may instead specify `standalone: false` in their `@Component` decorator. For these components, you instead import the `NgModule` in which the component is defined. See the full [`NgModule` guide](https://angular.dev/guide/ngmodules/overview) for details.

IMPORTANT: In Angular versions before 19.0.0, the `standalone` option defaults to `false`.

#### Showing components in a template

Every component defines a [CSS selector](https://developer.mozilla.org/docs/Learn/CSS/Building_blocks/Selectors):

```ts
@Component({
  selector: 'profile-photo',
  ...
})
export class ProfilePhoto { }
```

See [Component Selectors](https://angular.dev/guide/components/selectors) for details about which types of selectors Angular supports and guidance on choosing a selector.

You show a component by creating a matching HTML element in the template of _other_ components:

```ts
@Component({
  selector: 'profile-photo',
})
export class ProfilePhoto {}

@Component({
  imports: [ProfilePhoto],
  template: `<profile-photo />`,
})
export class UserProfile {}
```

Angular creates an instance of the component for every matching HTML element it encounters. The DOM element that matches a component's selector is referred to as that component's **host element**. The contents of a component's template are rendered inside its host element.

The DOM rendered by a component, corresponding to that component's template, is called that
component's **view**.

In composing components in this way, **you can think of your Angular application as a tree of components**.

This tree structure is important to understanding several other Angular concepts, including [dependency injection](https://angular.dev/guide/di) and [child queries](https://angular.dev/guide/components/queries).

## Standalone components and NgModules

Official page: https://angular.dev/guide/ngmodules/overview

IMPORTANT: The Angular team recommends using [standalone components](https://angular.dev/guide/components) instead of `NgModule` for all new code. Use this guide to understand existing code built with `@NgModule`.

Any components, directives, or pipes must be explicitly marked as `standalone: false` in order to be declared in an NgModule.

```typescript
@Component({
  // Mark this component as `standalone: false` so that it can be declared in an NgModule.
  standalone: false,
  /* ... */
})
export class CustomMenu {
  /* ... */
}
```

### The `forRoot` and `forChild` pattern

Some NgModules define a static `forRoot` method that accepts some configuration and returns an array of providers. The name "`forRoot`" is a convention that indicates that these providers are intended to be added exclusively to the _root_ of your application during bootstrap.

Any providers included in this way are eagerly loaded, increasing the JavaScript bundle size of your initial page load.

```typescript
bootstrapApplication(MyApplicationRoot, {
  providers: [CustomMenuModule.forRoot(/* some config */)],
});
```

Similarly, some NgModules may define a static `forChild` that indicates the providers are intended to be added to components within your application hierarchy.

```typescript
@Component({
  /* ... */
  providers: [CustomMenuModule.forChild(/* some config */)],
})
export class UserProfile {
  /* ... */
}
```


IMPORTANT: The Angular team recommends using [bootstrapApplication](https://angular.dev/api/platform-browser/bootstrapApplication) instead of `bootstrapModule` for all new code. Use this guide to understand existing applications bootstrapped with `@NgModule`.

## Where can you specify providers?

Official page: https://angular.dev/guide/di/defining-dependency-providers

Angular offers several levels where you can register providers, each with different implications for scope, lifecycle, and performance:

- [**Application bootstrap**](https://angular.dev/guide/di/defining-dependency-providers#application-bootstrap) - Global singletons available everywhere
- [**On an element (component or directive)**](https://angular.dev/guide/di/defining-dependency-providers#component-or-directive-providers) - Isolated instances for specific component trees
- [**Route**](https://angular.dev/guide/di/defining-dependency-providers#route-providers) - Feature-specific services for lazy-loaded modules

### Application bootstrap

Use application-level providers in `bootstrapApplication` when:

- **The service is used across multiple feature areas** - Services like HTTP clients, logging, or authentication that many parts of your app need
- **You want a true singleton** - One instance shared by the entire application
- **The service has no component-specific configuration** - General-purpose utilities that work the same everywhere
- **You're providing global configuration** - API endpoints, feature flags, or environment settings

```ts
// main.ts
bootstrapApplication(App, {
  providers: [
    {provide: API_BASE_URL, useValue: 'https://api.example.com'},
    {provide: INTERCEPTOR_TOKEN, useClass: AuthInterceptor, multi: true},
    LoggingService, // Used throughout the app
    {provide: ErrorHandler, useClass: GlobalErrorHandler},
  ],
});
```

**Benefits:**

- Single instance reduces memory usage
- Available everywhere without additional setup
- Easier to manage global state

**Drawbacks:**

- Always included in your JavaScript bundle, even if the value is never injected
- Cannot be easily customized per feature
- Harder to test individual components in isolation

#### Why provide during bootstrap instead of using `providedIn: 'root'`?

You might want a provider during bootstrap when:

- The provider has side-effects (e.g., installing the client-side router)
- The provider requires configuration (e.g., routes)
- You're using Angular's `provideSomething` pattern (e.g., `provideRouter`, `provideHttpClient`)

### Component or directive providers

Use component or directive providers when:

- **The service has component-specific state** - Form validators, component-specific caches, or UI state managers
- **You need isolated instances** - Each component needs its own copy of the service
- **The service is only used by one component tree** - Specialized services that don't need global access
- **You're creating reusable components** - Components that should work independently with their own services

```ts
// Specialized form component with its own validation service
@Component({
  selector: 'app-advanced-form',
  providers: [
    FormValidationService, // Each form gets its own validator
    {provide: FORM_CONFIG, useValue: {strictMode: true}},
  ],
})
export class AdvancedForm {}

// Modal component with isolated state management
@Component({
  selector: 'app-modal',
  providers: [
    ModalStateService, // Each modal manages its own state
  ],
})
export class Modal {}
```

**Benefits:**

- Better encapsulation and isolation
- Easier to test components individually
- Multiple instances can coexist with different configurations

**Drawbacks:**

- New instance created for each component (higher memory usage)
- No shared state between components
- Must be provided wherever needed
- Always included in the same JavaScript bundle as the component or directive, even if the value is never injected

NOTE: If multiple directives on the same element provide the same token, one will win, but which one is undefined.

### Route providers

Use route-level providers for:

- **Feature-specific services** - Services only needed for particular routes or feature modules
- **Lazy-loaded module dependencies** - Services that should only load with specific features
- **Route-specific configuration** - Settings that vary by application area

```ts
// routes.ts
export const routes: Routes = [
  {
    path: 'admin',
    providers: [
      AdminService, // Only loaded with admin routes
      {provide: FEATURE_FLAGS, useValue: {adminMode: true}},
    ],
    loadChildren: () => import('./admin/admin.routes'),
  },
  {
    path: 'shop',
    providers: [
      ShoppingCartService, // Isolated shopping state
      PaymentService,
    ],
    loadChildren: () => import('./shop/shop.routes'),
  },
];
```

Services provided at the route level are available to all components and directives within that route, as well as to its guards and resolvers.

Since these services are instantiated independently of the route’s components, they do not have direct access to route-specific information.

## Route Loading Strategies

Official page: https://angular.dev/guide/routing/loading-strategies

Understanding how and when routes and components load in Angular routing is crucial for building responsive web applications. Angular offers two primary strategies to control loading behavior:

1. **Eagerly loaded**: Routes and components that are loaded immediately
2. **Lazily loaded**: Routes and components loaded only when needed

Each approach offers distinct advantages for different scenarios.

### Eagerly loaded components

When you define a route with the [`component`](https://angular.dev/api/router/Route#component) property, the referenced component is eagerly loaded as part of the same JavaScript bundle as the route configuration.

```ts
import {Routes} from '@angular/router';
import {HomePage} from './components/home/home-page';
import {LoginPage} from './components/auth/login-page';

export const routes: Routes = [
  // HomePage and LoginPage are both directly referenced in this config,
  // so their code is eagerly included in the same JavaScript bundle as this file.
  {
    path: '',
    component: HomePage,
  },
  {
    path: 'login',
    component: LoginPage,
  },
];
```

Eagerly loading route components like this means that the browser has to download and parse all of the JavaScript for these components as part of your initial page load, but the components are available to Angular immediately.

While including more JavaScript in your initial page load leads to slower initial load times, this can lead to more seamless transitions as the user navigates through an application.

### Lazily loaded components and routes

You can use the [`loadComponent`](https://angular.dev/api/router/Route#loadComponent) property to lazily load the JavaScript for a component at the point at which that route would become active. The [`loadChildren`](https://angular.dev/api/router/Route#loadChildren) property lazily loads child routes during route matching.

```ts
import {Routes} from '@angular/router';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./components/auth/login-page'),
  },
  {
    path: 'admin',
    loadComponent: () => import('./admin/admin.component'),
    loadChildren: () => import('./admin/admin.routes'),
  },
];
```

The [`loadComponent`](https://angular.dev/api/router/Route#loadComponent) and [`loadChildren`](https://angular.dev/api/router/Route#loadChildren) properties accept a loader function that returns a Promise that resolves to an Angular component or a set of routes respectively. In most cases, this function uses the standard [JavaScript dynamic import API](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/import). You can, however, use any arbitrary async loader function.

If the lazily loaded file uses a `default` export, you can return the `import()` promise directly without an additional `.then` call to select the exported class.

Lazily loading routes can significantly improve the load speed of your Angular application by removing large portions of JavaScript from the initial bundle. These portions of your code compile into separate JavaScript "chunks" that the router requests only when the user visits the corresponding route.

### Injection context lazy loading

The Router executes [`loadComponent`](https://angular.dev/api/router/Route#loadComponent) and [`loadChildren`](https://angular.dev/api/router/Route#loadChildren) within the **injection context of the current route**, allowing you to call [`inject`](https://angular.dev/api/core/inject)inside these loader functions to access providers declared on that route, inherited from parent routes through hierarchical dependency injection, or available globally. This enables context-aware lazy loading.

```ts
import {Routes} from '@angular/router';
import {inject} from '@angular/core';
import {FeatureFlags} from './feature-flags';

export const routes: Routes = [
  {
    path: 'dashboard',
    // Runs inside the route's injection context
    loadComponent: () => {
      const flags = inject(FeatureFlags);
      return flags.isPremium
        ? import('./dashboard/premium-dashboard')
        : import('./dashboard/basic-dashboard');
    },
  },
];
```

### Should I use an eager or a lazy route?

There are many factors to consider when deciding on whether a route should be eager or lazy.

In general, eager loading is recommended for primary landing page(s) while other pages would be lazy-loaded.

NOTE: While lazy routes have the upfront performance benefit of reducing the amount of initial data requested by the user, it adds future data requests that could be undesirable. This is particularly true when dealing with nested lazy loading at multiple levels, which can significantly impact performance.

## Migrate an existing Angular project to standalone

Official page: https://angular.dev/reference/migrations/standalone

**Standalone components** provide a simplified way to build Angular applications. Standalone components, directives, and pipes aim to streamline the authoring experience by reducing the need for `NgModule`s. Existing applications can optionally and incrementally adopt the new standalone style without any breaking changes.


This schematic helps to transform components, directive and pipes in existing projects to become standalone. The schematic aims to transform as much code as possible automatically, but it may require some manual fixes by the project author.

Run the schematic using the following command:

```shell
ng generate @angular/core:standalone
```

### Before updating

Before using the schematic, please ensure that the project:

1. Is using Angular 15.2.0 or later.
2. Builds without any compilation errors.
3. Is on a clean Git branch and all work is saved.


### Migrations steps

The migration process is composed of three steps. You'll have to run it multiple times and check manually that the project builds and behaves as expected.

NOTE: While the schematic can automatically update most code, some edge cases require developer intervention.
You should plan to apply manual fixes after each step of the migration. Additionally, the new code generated by the schematic may not match your code's formatting rules.

Run the migration in the order listed below, verifying that your code builds and runs between each step:

1. Run `ng g @angular/core:standalone` and select "Convert all components, directives and pipes to standalone"
2. Run `ng g @angular/core:standalone` and select "Remove unnecessary NgModule classes"
3. Run `ng g @angular/core:standalone` and select "Bootstrap the project using standalone APIs"
4. Run any linting and formatting checks, fix any failures, and commit the result


### Migration modes

The migration has the following modes:

1. Convert declarations to standalone.
2. Remove unnecessary NgModules.
3. Switch to standalone bootstrapping API.
   You should run these migrations in the order given.

#### Convert declarations to standalone

In this mode, the migration converts all components, directives and pipes to standalone by removing `standalone: false` and adding dependencies to their `imports` array.

HELPFUL: The schematic ignores NgModules which bootstrap a component during this step because they are likely root modules used by `bootstrapModule` rather than the standalone-compatible `bootstrapApplication`. The schematic converts these declarations automatically as a part of the ["Switch to standalone bootstrapping API"](https://angular.dev/reference/migrations/standalone#switch-to-standalone-bootstrapping-api) step.

**Before:**

```typescript
// shared.module.ts
@NgModule({
  imports: [CommonModule],
  declarations: [Greeter],
  exports: [Greeter],
})
export class SharedModule {}
```

```ts
// greeter.ts
@Component({
  selector: 'greeter',
  template: '<div *ngIf="showGreeting">Hello</div>',
  standalone: false,
})
export class Greeter {
  showGreeting = true;
}
```

**After:**

```typescript
// shared.module.ts
@NgModule({
  imports: [CommonModule, Greeter],
  exports: [Greeter],
})
export class SharedModule {}
```

```ts
// greeter.ts
@Component({
  selector: 'greeter',
  template: '<div *ngIf="showGreeting">Hello</div>',
  imports: [NgIf],
})
export class Greeter {
  showGreeting = true;
}
```

#### Remove unnecessary NgModules

After converting all declarations to standalone, many NgModules can be safely removed. This step deletes such module declarations and as many corresponding references as possible. If the migration cannot delete a reference automatically, it leaves the following TODO comment so that you can delete the NgModule manually:

```typescript
/* TODO(standalone-migration): clean up removed NgModule reference manually */
```

The migration considers a module safe to remove if that module:

- Has no `declarations`.
- Has no `providers`.
- Has no `bootstrap` components.
- Has no `imports` that reference a `ModuleWithProviders` symbol or a module that can't be removed.
- Has no class members. Empty constructors are ignored.

**Before:**

```typescript
// importer.module.ts
@NgModule({
  imports: [FooComponent, BarPipe],
  exports: [FooComponent, BarPipe],
})
export class ImporterModule {}
```

**After:**

```typescript
// importer.module.ts
// Does not exist!
```

#### Switch to standalone bootstrapping API

This step converts any usages of `bootstrapModule` to the new, standalone-based `bootstrapApplication`. It also removes `standalone: false` from the root component and deletes the root NgModule. If the root module has any `providers` or `imports`, the migration attempts to copy as much of this configuration as possible into the new bootstrap call.

**Before:**

```typescript
// ./app/app.module.ts
import {NgModule} from '@angular/core';
import {App} from './app';

@NgModule({
  declarations: [App],
  bootstrap: [App],
})
export class AppModule {}
```

```typescript
// ./app/app.ts
@Component({
  selector: 'app',
  template: 'hello',
  standalone: false,
})
export class App {}
```

```typescript
// ./main.ts
import {platformBrowser} from '@angular/platform-browser';
import {AppModule} from './app/app.module';

platformBrowser()
  .bootstrapModule(AppModule)
  .catch((e) => console.error(e));
```

**After:**

```typescript
// ./app/app.module.ts
// Does not exist!
```

```typescript
// ./app/app.ts
@Component({
  selector: 'app',
  template: 'hello',
})
export class App {}
```

```typescript
// ./main.ts
import {bootstrapApplication} from '@angular/platform-browser';
import {App} from './app';

bootstrapApplication(App).catch((e) => console.error(e));
```
